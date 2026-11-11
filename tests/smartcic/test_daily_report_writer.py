"""daily_report_writer（stdio MCP server）測試，HTTP 一律以假的 http 函式取代。

執行：pytest tests/smartcic
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[2] / "smartcic" / "mcp" / "daily_report_writer.py"
spec = importlib.util.spec_from_file_location("daily_report_writer", SCRIPT)
w = importlib.util.module_from_spec(spec)
sys.modules["daily_report_writer"] = w
spec.loader.exec_module(w)

USER = "U" + "a" * 32
USER_B = "U" + "b" * 32
USER_DUP = "U" + "d" * 32
REPORT_DB = "0" * 31 + "1"
ROSTER_DB = "0" * 31 + "2"
ENV = {
    "NOTION_TOKEN": "secret",
    "MC_API_KEY": "mc-key",
    "NOTION_DAILY_REPORT_DATABASE_ID": REPORT_DB,
    "NOTION_ROSTER_DATABASE_ID": ROSTER_DB,
}
REPORT = {
    "sender_id": USER,
    "messages": [
        {"sent_at": "2026-11-05T19:00:00+08:00", "text": "第一則"},
        {"sent_at": "2026-11-05T21:10:24+08:00", "text": "第二則"},
    ],
    "combined_text": "第一則\n第二則",
}
# Notion 回傳的 id 本來就帶連字號；模型曾把它抄成少一個 - 的版本（見 PAGE_B 的變形）
PAGE_A = "1a2b3c4d-5e6f-4a1b-8c2d-3e4f5a6b7c8d"
PAGE_B = "9f8e7d6c-5b4a-4c3d-8e2f-1a0b9c8d7e6f"


def roster_page(page_id, name, sender_id):
    user_id = [{"plain_text": sender_id}] if sender_id else []
    return {
        "id": page_id,
        "properties": {
            "姓名": {"title": [{"plain_text": name}]},
            "LINE userId": {"rich_text": user_id},
        },
    }


ROSTER = [
    roster_page(PAGE_A, "王小明", USER),
    roster_page(PAGE_B, "李大華", USER_B),
    roster_page("a" * 32, "陳小美", ""),
    roster_page("b" * 32, "甲", USER_DUP),
    roster_page("c" * 32, "乙", USER_DUP),
]


class FakeHttp:
    """模擬 MessageCenter /mcp 與 Notion API，記錄所有呼叫

    roster 依 page_size 切頁回傳（has_more / next_cursor）；reports 是 MessageCenter 有訊息的人。
    """

    def __init__(self, existing=(), reports=(REPORT,), roster=ROSTER, notion_error=None,
                 page_size=None):  # fmt: skip
        self.existing = list(existing)
        self.reports = list(reports)
        self.roster = list(roster)
        self.notion_error = notion_error
        self.page_size = page_size
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if url.endswith("/mcp"):
            asked = body["params"]["arguments"]["sender_ids"]
            reports = [r for r in self.reports if r["sender_id"] in asked]
            content = {
                "timezone": "Asia/Taipei",
                "reports": reports,
                "missing": [s for s in asked if s not in {r["sender_id"] for r in reports}],
            }
            return 200, {"jsonrpc": "2.0", "id": 1, "result": {"structuredContent": content}}
        if self.notion_error:
            return self.notion_error
        if method == "GET":
            ds = "ds-roster" if url.endswith(ROSTER_DB) else "ds-1"
            return 200, {"data_sources": [{"id": ds}]}
        if url.endswith("/ds-roster/query"):
            size = self.page_size or len(self.roster) or 1
            start = int(body.get("start_cursor") or 0)
            chunk = self.roster[start : start + size]
            more = start + size < len(self.roster)
            return 200, {"results": chunk, "has_more": more, "next_cursor": str(start + size)}
        if url.endswith("/query"):
            return 200, {"results": self.existing, "has_more": False}
        if method == "POST":
            return 200, {"id": "new-page", "url": "https://notion.so/new"}
        if method == "PATCH":
            return 200, {"id": url.rsplit("/", 1)[-1]}
        raise AssertionError((method, url))

    def notion_writes(self):
        return [c for c in self.calls if c[1].endswith("/pages") or c[0] == "PATCH"]

    def mc_calls(self):
        return [c for c in self.calls if c[1].endswith("/mcp")]


def args(**overrides):
    values = {"report_date": "2026-11-05", "sender_id": USER}
    values.update(overrides)
    return values


class FakeSSLContext:
    def __init__(self, ca_count):
        self.ca_count = ca_count
        self.loaded = []

    def cert_store_stats(self):
        return {"x509_ca": self.ca_count}

    def load_verify_locations(self, cafile=None):
        self.loaded.append(cafile)


class SSLContextTest(unittest.TestCase):
    def build(self, ca_count, bundles, env=None):
        fake = FakeSSLContext(ca_count)
        with mock.patch.object(w.ssl, "create_default_context", return_value=fake):
            self.assertIs(w.build_ssl_context(bundles, env or {}), fake)
        return fake

    def test_empty_trust_store_loads_first_existing_system_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "ca.pem"
            bundle.write_text("x")
            fake = self.build(0, (str(Path(tmp) / "missing.pem"), str(bundle)))
        self.assertEqual(fake.loaded, [str(bundle)])

    def test_populated_trust_store_is_left_alone(self):
        with tempfile.NamedTemporaryFile() as bundle:
            self.assertEqual(self.build(120, (bundle.name,)).loaded, [])

    def test_ssl_cert_file_is_respected(self):
        with tempfile.NamedTemporaryFile() as bundle:
            fake = self.build(0, (bundle.name,), {"SSL_CERT_FILE": "/custom/ca.pem"})
        self.assertEqual(fake.loaded, [])


# 實際遇過的訊息型態：閒聊在前、五段日報在後（模型曾誤判為非日報）
CHAT_THEN_REPORT = """這幾台有加進去錄影了嗎
有設定好的設備名稱嗎
1. 出勤時間
- 到廠：8:30
- 出廠：17:30
2. 今日工作內容
A案驗收報告更新
3. 施工進度
無
4. 問題與待協助事項
無
5. 明日工作計畫
B棟1F機房佈線"""
CHAT_ONLY = "客戶的警報設定要怎麼通知到監控平台呢\nOk 可以查這幾台設備\n有"


class DetectReportSectionsTest(unittest.TestCase):
    def test_report_after_chat_is_detected(self):
        self.assertEqual(
            w.detect_report_sections(CHAT_THEN_REPORT),
            ["到廠", "出廠", "今日工作", "施工進度", "問題與待協助", "明日計畫"],
        )

    def test_chat_only_has_no_sections(self):
        self.assertEqual(w.detect_report_sections(CHAT_ONLY), [])
        self.assertEqual(w.detect_report_sections(""), [])

    def test_variants(self):
        self.assertEqual(w.detect_report_sections("【今天工作】設定\n明天計劃：查修"),
                         ["今日工作", "明日計畫"])  # fmt: skip

    def test_roster_reports_carry_sections(self):
        http = FakeHttp(reports=({**REPORT, "combined_text": CHAT_THEN_REPORT},))
        result = w.get_roster_daily_reports({"report_date": "2026-11-05"}, ENV, http)
        self.assertIn("到廠", result["reports"][0]["report_sections"])


class NormalizePageIdTest(unittest.TestCase):
    def test_accepts_dashed_compact_and_misplaced_dashes(self):
        for value in (PAGE_B, PAGE_B.replace("-", ""), "9f8e7d6c-5b4a-4c3d-8e2f1a0b9c8d7e6f",
                      PAGE_B.upper()):  # fmt: skip
            with self.subTest(value=value):
                self.assertEqual(w.normalize_page_id(value), PAGE_B)

    def test_rejects_invalid(self):
        for value in ("", "9f8e7d6c-5b4a-4c3d-8e2f-1a0b9c8d7e6", "z" * 32, "roster-1"):
            with self.subTest(value=value), self.assertRaises(w.ToolError):
                w.normalize_page_id(value)


class RosterTest(unittest.TestCase):
    def test_paginates_and_splits_missing_userid_and_duplicates(self):
        http = FakeHttp(page_size=2)
        client = w.NotionClient("secret", http)
        roster = w.load_roster(client, ROSTER_DB)

        self.assertEqual([m.name for m in roster.members], ["王小明", "李大華"])
        self.assertEqual(roster.no_line_user_id, ("陳小美",))
        self.assertEqual(roster.duplicated, ("甲", "乙"))
        queries = [c for c in http.calls if c[1].endswith("/ds-roster/query")]
        self.assertEqual(len(queries), 3)
        self.assertEqual(queries[0][3]["filter"], {"property": "啟用", "checkbox": {"equals": True}})
        self.assertEqual(queries[1][3]["start_cursor"], "2")
        self.assertEqual(roster.members[1].page_id, PAGE_B)


class GetRosterDailyReportsTest(unittest.TestCase):
    def test_one_messagecenter_call_and_name_lists(self):
        http = FakeHttp()
        result = w.get_roster_daily_reports({"report_date": "2026-11-05"}, ENV, http)

        mc = http.mc_calls()
        self.assertEqual(len(mc), 1)
        self.assertEqual(mc[0][2]["X-API-Key"], "mc-key")
        self.assertEqual(
            mc[0][3]["params"]["arguments"],
            {"report_date": "2026-11-05", "sender_ids": [USER, USER_B]},
        )
        self.assertEqual(
            result["reports"],
            [
                {
                    "sender_id": USER,
                    "name": "王小明",
                    "combined_text": "第一則\n第二則",
                    "report_sections": [],
                }
            ],
        )
        self.assertEqual(result["missing"], ["李大華"])
        self.assertEqual(result["no_line_user_id"], ["陳小美"])
        self.assertEqual(result["duplicated"], ["甲", "乙"])
        self.assertEqual(result["timezone"], "Asia/Taipei")
        self.assertEqual(http.notion_writes(), [])

    def test_ignores_messagecenter_reports_outside_roster(self):
        stranger = {**REPORT, "sender_id": "U" + "f" * 32}
        http = FakeHttp(reports=(REPORT, stranger))
        result = w.get_roster_daily_reports({"report_date": "2026-11-05"}, ENV, http)
        self.assertEqual([r["sender_id"] for r in result["reports"]], [USER])

    def test_unresolved_url_placeholder_falls_back_to_default(self):
        """Hermes 遇到未設定的 ${VAR} 會原樣傳入佔位字串，要當成未設定"""
        env = {**ENV, "MC_MCP_URL": "${MC_MCP_URL}"}
        http = FakeHttp()
        w.get_roster_daily_reports({"report_date": "2026-11-05"}, env, http)
        self.assertEqual(http.mc_calls()[0][1], w.DEFAULT_MC_MCP_URL)

    def test_database_ids_are_required(self):
        for key in ("NOTION_ROSTER_DATABASE_ID", "NOTION_DAILY_REPORT_DATABASE_ID"):
            for missing in ({}, {key: "${" + key + "}"}):
                with self.subTest(key=key, missing=missing):
                    env = {k: v for k, v in ENV.items() if k != key} | missing
                    http = FakeHttp()
                    with self.assertRaisesRegex(w.ToolError, f"{key} is not set"):
                        if key == "NOTION_ROSTER_DATABASE_ID":
                            w.get_roster_daily_reports({"report_date": "2026-11-05"}, env, http)
                        else:
                            w.upsert_daily_report(args(), env, http)
                    self.assertEqual(http.notion_writes(), [])

    def test_unresolved_secret_placeholder_is_missing(self):
        env = {**ENV, "MC_API_KEY": "${MC_API_KEY}"}
        with self.assertRaisesRegex(w.ToolError, "MC_API_KEY is not set"):
            w.get_roster_daily_reports({"report_date": "2026-11-05"}, env, FakeHttp())

    def test_empty_roster_skips_messagecenter(self):
        http = FakeHttp(roster=())
        result = w.get_roster_daily_reports({"report_date": "2026-11-05"}, ENV, http)
        self.assertEqual(result["reports"], [])
        self.assertEqual(result["missing"], [])
        self.assertEqual(http.mc_calls(), [])

    def test_validation(self):
        with self.assertRaisesRegex(w.ToolError, "YYYY-MM-DD"):
            w.get_roster_daily_reports({"report_date": "11/05"}, ENV, FakeHttp())
        with self.assertRaisesRegex(w.ToolError, "MC_API_KEY"):
            w.get_roster_daily_reports({"report_date": "2026-11-05"}, {"NOTION_TOKEN": "t"},
                                       FakeHttp())  # fmt: skip


class UpsertTest(unittest.TestCase):
    def test_creates_with_roster_name_relation_raw_text_and_submit_time(self):
        http = FakeHttp()
        result = w.upsert_daily_report(args(today_work="驗收報告"), ENV, http)

        self.assertEqual(result["action"], "created")
        self.assertEqual(result["title"], "王小明 2026-11-05")
        self.assertEqual(result["name"], "王小明")
        method, url, headers, body = http.calls[-1]
        self.assertEqual((method, url), ("POST", "https://api.notion.com/v1/pages"))
        self.assertEqual(headers["Authorization"], "Bearer secret")
        self.assertEqual(body["parent"], {"type": "data_source_id", "data_source_id": "ds-1"})
        props = body["properties"]
        self.assertEqual(props["原文"]["rich_text"][0]["text"]["content"], "第一則\n第二則")
        self.assertEqual(props["提交時間"], {"date": {"start": "2026-11-05T21:10:24+08:00"}})
        self.assertEqual(props["人員"], {"relation": [{"id": PAGE_A}]})
        self.assertEqual(props["施工進度"], {"rich_text": []})

    def test_model_supplied_name_and_roster_page_id_are_ignored(self):
        http = FakeHttp()
        w.upsert_daily_report(
            args(name="張三", roster_page_id="9f8e7d6c-5b4a-4c3d-8e2f1a0b9c8d7e6f"), ENV, http
        )
        props = http.calls[-1][3]["properties"]
        self.assertEqual(props["標題"]["title"][0]["text"]["content"], "王小明 2026-11-05")
        self.assertEqual(props["人員"], {"relation": [{"id": PAGE_A}]})

    def test_messagecenter_called_with_single_sender(self):
        http = FakeHttp()
        w.upsert_daily_report(args(), ENV, http)
        mc = http.mc_calls()
        self.assertEqual(len(mc), 1)
        self.assertEqual(mc[0][1], "http://localhost:8000/mcp")
        self.assertEqual(
            mc[0][3]["params"]["arguments"], {"report_date": "2026-11-05", "sender_ids": [USER]}
        )

    def test_rejects_sender_outside_roster_or_duplicated(self):
        for sender_id in ("U" + "f" * 32, USER_DUP):
            http = FakeHttp()
            with self.subTest(sender_id=sender_id):
                with self.assertRaisesRegex(w.ToolError, "not in the active roster"):
                    w.upsert_daily_report(args(sender_id=sender_id), ENV, http)
                self.assertEqual(http.notion_writes(), [])
                self.assertEqual(http.mc_calls(), [])

    def test_updates_when_one_row_exists(self):
        http = FakeHttp(existing=[{"id": "page-9"}])
        result = w.upsert_daily_report(args(), ENV, http)
        self.assertEqual(result["action"], "updated")
        self.assertEqual(http.calls[-1][:2], ("PATCH", "https://api.notion.com/v1/pages/page-9"))

    def test_duplicates_raise_without_writing(self):
        http = FakeHttp(existing=[{"id": "a"}, {"id": "b"}])
        with self.assertRaisesRegex(w.ToolError, "Duplicate"):
            w.upsert_daily_report(args(), ENV, http)
        self.assertEqual(http.notion_writes(), [])

    def test_no_messages_raises_without_writing(self):
        http = FakeHttp(reports=())
        with self.assertRaisesRegex(w.ToolError, "No text messages from 王小明"):
            w.upsert_daily_report(args(), ENV, http)
        self.assertEqual(http.notion_writes(), [])

    def test_notion_http_error(self):
        http = FakeHttp(notion_error=(401, {"message": "API token is invalid."}))
        with self.assertRaisesRegex(w.ToolError, "API token is invalid"):
            w.upsert_daily_report(args(), ENV, http)

    def test_literal_backslash_n_becomes_newline(self):
        http = FakeHttp()
        w.upsert_daily_report(args(today_work="A\\nB", progress="P5 60%\nP6 97%"), ENV, http)
        props = http.calls[-1][3]["properties"]
        self.assertEqual(props["今日工作"]["rich_text"][0]["text"]["content"], "A\nB")
        self.assertEqual(props["施工進度"]["rich_text"][0]["text"]["content"], "P5 60%\nP6 97%")

    def test_validation(self):
        for bad in (args(report_date="yesterday"), args(sender_id=" ")):
            with self.subTest(bad=bad), self.assertRaises(w.ToolError):
                w.upsert_daily_report(bad, ENV, FakeHttp())
        with self.assertRaisesRegex(w.ToolError, "NOTION_TOKEN"):
            w.upsert_daily_report(args(), {"MC_API_KEY": "k"}, FakeHttp())
        with self.assertRaisesRegex(w.ToolError, "MC_API_KEY"):
            w.upsert_daily_report(args(), {"NOTION_TOKEN": "t"}, FakeHttp())

    def test_long_text_is_chunked(self):
        row = w.DailyReportRow(
            title="t", report_date="2026-11-05", sender_id=USER, roster_page_id="",
            arrive_time="", leave_time="", today_work="", progress="", issues="",
            tomorrow_plan="", raw_text="字" * 4500, submitted_at=None,
        )  # fmt: skip
        props = w.build_properties(row)
        sizes = [len(p["text"]["content"]) for p in props["原文"]["rich_text"]]
        self.assertEqual(sizes, [2000, 2000, 500])
        self.assertNotIn("人員", props)
        self.assertEqual(props["提交時間"], {"date": None})


class JsonRpcTest(unittest.TestCase):
    def rpc(self, method, params=None, http=None):
        message = {"jsonrpc": "2.0", "id": 7, "method": method, "params": params or {}}
        return w.handle(message, ENV, http or FakeHttp())

    def test_initialize_echoes_protocol(self):
        result = self.rpc("initialize", {"protocolVersion": "2025-03-26"})["result"]
        self.assertEqual(result["protocolVersion"], "2025-03-26")
        self.assertEqual(result["serverInfo"]["name"], "daily_report_writer")

    def test_notification_gets_no_reply(self):
        self.assertIsNone(w.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, ENV))

    def test_tools_list(self):
        tools = self.rpc("tools/list")["result"]["tools"]
        names = [t["name"] for t in tools]
        self.assertEqual(names, ["get_roster_daily_reports", "upsert_daily_report"])
        for tool in tools:
            props = tool["inputSchema"]["properties"]
            self.assertTrue(all(p["type"] == "string" for p in props.values()))
        upsert = tools[1]["inputSchema"]["properties"]
        self.assertNotIn("name", upsert)
        self.assertNotIn("roster_page_id", upsert)
        self.assertTrue(tools[0]["annotations"]["readOnlyHint"])

    def test_tools_call_success_and_stringified_arguments(self):
        for arguments in (args(), json.dumps(args())):
            with self.subTest(type=type(arguments).__name__):
                params = {"name": "upsert_daily_report", "arguments": arguments}
                result = self.rpc("tools/call", params)["result"]
                self.assertNotIn("isError", result)
                self.assertEqual(result["structuredContent"]["action"], "created")

    def test_tools_call_get_roster_daily_reports(self):
        params = {"name": "get_roster_daily_reports", "arguments": {"report_date": "2026-11-05"}}
        result = self.rpc("tools/call", params)["result"]
        self.assertEqual(result["structuredContent"]["missing"], ["李大華"])

    def test_tools_call_error_is_tool_error(self):
        params = {"name": "upsert_daily_report", "arguments": args(report_date="x")}
        result = self.rpc("tools/call", params)["result"]
        self.assertTrue(result["isError"])
        self.assertIn("YYYY-MM-DD", result["content"][0]["text"])

    def test_unknown_method_and_tool(self):
        self.assertEqual(self.rpc("resources/list")["error"]["code"], -32601)
        self.assertEqual(self.rpc("tools/call", {"name": "nope"})["error"]["code"], -32602)

    def test_stdio_roundtrip(self):
        lines = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        stdin = "\n".join(json.dumps(m) for m in lines) + "\nnot json\n"
        proc = subprocess.run(
            [sys.executable, str(SCRIPT)],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        replies = [json.loads(line) for line in proc.stdout.splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2, None])
        self.assertEqual(replies[2]["error"]["code"], -32700)


if __name__ == "__main__":
    unittest.main()
