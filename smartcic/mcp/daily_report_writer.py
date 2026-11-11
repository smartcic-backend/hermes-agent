#!/usr/bin/env python3
"""PM 日報的 stdio MCP server（由 Hermes 啟動，只用標準函式庫）。

為什麼有這支：地端模型手組 Notion 巢狀 JSON 會跳脫錯誤，
也會抄錯 page id、編造名單上沒有的人。因此模型只負責「把日報原文拆成五段」，
其餘都由程式處理：

- `get_roster_daily_reports(report_date)`：讀 Notion 應交日報名單（啟用中），一次向
  MessageCenter（唯讀）取得全員當日訊息，回傳已交的原文和未交、缺 userId、重複的姓名清單。
- `upsert_daily_report(report_date, sender_id, 五段)`：用 sender_id 反查名單，帶入姓名與
  「人員」relation；原文與提交時間向 MessageCenter 取得，不經模型轉述。

環境變數：
  NOTION_TOKEN                     Notion integration token（必填）
  MC_API_KEY                       MessageCenter API key（必填）
  MC_MCP_URL                       預設 http://localhost:8000/mcp
                                   （Hermes 跑在 Docker 時用 http://host.docker.internal:8000/mcp）
  NOTION_DAILY_REPORT_DATABASE_ID  Notion「日報」資料庫 id（必填）
  NOTION_ROSTER_DATABASE_ID        Notion「應交日報名單」資料庫 id（必填）

Hermes 遇到未設定的 ``${VAR}`` 會把字面佔位字串原樣傳進來，這裡一律視為未設定。

協定：stdin/stdout 每行一則 JSON-RPC 2.0 訊息（MCP stdio transport）。log 一律寫 stderr。
"""

from __future__ import annotations

import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

SERVER_NAME = "daily_report_writer"
SERVER_VERSION = "0.2.0"
DEFAULT_PROTOCOL = "2025-06-18"
NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"
RICH_TEXT_LIMIT = 2000
DEFAULT_MC_MCP_URL = "http://localhost:8000/mcp"
ROSTER_PAGE_SIZE = 100
MAX_ROSTER_PAGES = 20
TIMEOUT = 30

SECTIONS = ("arrive_time", "leave_time", "today_work", "progress", "issues", "tomorrow_plan")

# 預設 trust store 是空的時（例如 python.org 版 Python 沒跑 Install Certificates.command），
# 改用作業系統的 CA bundle，依序取第一個存在的
SYSTEM_CA_BUNDLES = (
    "/etc/ssl/cert.pem",  # macOS
    "/etc/ssl/certs/ca-certificates.crt",  # Debian / Ubuntu / Docker
    "/etc/pki/tls/certs/ca-bundle.crt",  # RHEL / CentOS
)


def build_ssl_context(
    bundles: tuple[str, ...] = SYSTEM_CA_BUNDLES, env: dict | None = None
) -> ssl.SSLContext:
    """預設 SSL context；沒有載入任何 CA 且使用者沒設 SSL_CERT_FILE 時，補載系統 CA bundle"""
    context = ssl.create_default_context()
    env = os.environ if env is None else env
    if context.cert_store_stats().get("x509_ca") or env.get("SSL_CERT_FILE"):
        return context
    for bundle in bundles:
        if os.path.isfile(bundle):
            context.load_verify_locations(cafile=bundle)
            break
    return context


_SSL_CONTEXT = build_ssl_context()

# (method, url, headers, body) -> (status, parsed JSON)
Http = Callable[[str, str, dict, "dict | None"], "tuple[int, Any]"]


class ToolError(RuntimeError):
    """回給模型的錯誤（isError: true），訊息要能直接記入摘要"""


_PLACEHOLDER = re.compile(r"\$\{[^}]*\}")


def env_value(env: dict, key: str) -> str:
    """取環境變數；空白或仍是 ``${VAR}`` 佔位字串時回傳空字串"""
    value = str(env.get(key) or "").strip()
    return "" if _PLACEHOLDER.search(value) else value


def required_env(env: dict, key: str) -> str:
    """必填的環境變數；未設定時回報要設哪一個"""
    value = env_value(env, key)
    if not value:
        raise ToolError(f"{key} is not set for daily_report_writer.")
    return value


def urllib_http(method: str, url: str, headers: dict, body: dict | None) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=_SSL_CONTEXT) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    except urllib.error.URLError as exc:
        raise ToolError(f"{method} {url} failed: {exc.reason}") from exc
    try:
        return status, json.loads(raw or b"null")
    except ValueError:
        return status, {"message": raw.decode(errors="replace")[:500]}


# ------------------------------------------------------------------ Notion


def normalize_page_id(value: str) -> str:
    """接受有無連字號（或連字號放錯）的 Notion id，回傳 8-4-4-4-12 格式；不合法時報錯"""
    compact = str(value).strip().replace("-", "").lower()
    if not re.fullmatch(r"[0-9a-f]{32}", compact):
        raise ToolError(f"Invalid Notion page id: {value!r}")
    c = compact
    return f"{c[:8]}-{c[8:12]}-{c[12:16]}-{c[16:20]}-{c[20:]}"


@dataclass(frozen=True)
class DailyReportRow:
    title: str
    report_date: str
    sender_id: str
    roster_page_id: str
    arrive_time: str
    leave_time: str
    today_work: str
    progress: str
    issues: str
    tomorrow_plan: str
    raw_text: str
    submitted_at: str | None


def rich_text(value: str) -> dict:
    """空字串 → 清空；超過 2000 字拆成多段"""
    chunks = [value[i : i + RICH_TEXT_LIMIT] for i in range(0, len(value), RICH_TEXT_LIMIT)]
    return {"rich_text": [{"type": "text", "text": {"content": c}} for c in chunks]}


def build_properties(row: DailyReportRow) -> dict:
    properties = {
        "標題": {"title": [{"type": "text", "text": {"content": row.title}}]},
        "日期": {"date": {"start": row.report_date}},
        "LINE userId": rich_text(row.sender_id),
        "到廠": rich_text(row.arrive_time),
        "出廠": rich_text(row.leave_time),
        "今日工作": rich_text(row.today_work),
        "施工進度": rich_text(row.progress),
        "問題與待協助": rich_text(row.issues),
        "明日計畫": rich_text(row.tomorrow_plan),
        "原文": rich_text(row.raw_text),
        "提交時間": {"date": {"start": row.submitted_at} if row.submitted_at else None},
    }
    if row.roster_page_id:
        properties["人員"] = {"relation": [{"id": normalize_page_id(row.roster_page_id)}]}
    return properties


class NotionClient:
    def __init__(self, token: str, http: Http = urllib_http):
        if not token:
            raise ToolError("NOTION_TOKEN is not set for daily_report_writer.")
        self.token = token
        self.http = http

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }
        status, data = self.http(method, f"{NOTION_API}{path}", headers, body)
        if status >= 400:
            message = data.get("message") if isinstance(data, dict) else data
            raise ToolError(f"Notion {method} {path} failed ({status}): {message}")
        return data

    def data_source_id(self, database_id: str) -> str:
        sources = self._request("GET", f"/databases/{database_id}").get("data_sources") or []
        if not sources:
            raise ToolError(f"Notion database {database_id} has no data source.")
        return sources[0]["id"]

    def query_all(self, data_source_id: str, query: dict) -> list[dict]:
        """依 has_more / next_cursor 取完所有結果"""
        results: list[dict] = []
        body = {**query, "page_size": ROSTER_PAGE_SIZE}
        for _ in range(MAX_ROSTER_PAGES):
            data = self._request("POST", f"/data_sources/{data_source_id}/query", body)
            results.extend(data.get("results") or [])
            if not data.get("has_more") or not data.get("next_cursor"):
                return results
            body = {**body, "start_cursor": data["next_cursor"]}
        raise ToolError(f"Notion query returned more than {MAX_ROSTER_PAGES} pages.")

    def upsert_daily_report(self, database_id: str, row: DailyReportRow) -> dict:
        """同人同日：0 筆新增、1 筆更新、2 筆以上報錯（不動資料）"""
        data_source_id = self.data_source_id(database_id)
        query = {
            "filter": {
                "and": [
                    {"property": "LINE userId", "rich_text": {"equals": row.sender_id}},
                    {"property": "日期", "date": {"equals": row.report_date}},
                ]
            },
            "page_size": 2,
        }
        existing = self._request("POST", f"/data_sources/{data_source_id}/query", query)["results"]
        if len(existing) > 1:
            raise ToolError("Duplicate daily report rows in Notion; please clean up manually.")
        properties = build_properties(row)
        if existing:
            page = self._request("PATCH", f"/pages/{existing[0]['id']}", {"properties": properties})
            action = "updated"
        else:
            parent = {"type": "data_source_id", "data_source_id": data_source_id}
            page = self._request("POST", "/pages", {"parent": parent, "properties": properties})
            action = "created"
        return {"action": action, "page_id": page["id"], "url": page.get("url", "")}


# ----------------------------------------------------------------- roster


@dataclass(frozen=True)
class Member:
    page_id: str
    name: str
    sender_id: str


@dataclass(frozen=True)
class Roster:
    members: tuple[Member, ...]  # 有 LINE userId 且不重複，名單順序
    no_line_user_id: tuple[str, ...]
    duplicated: tuple[str, ...]

    def find(self, sender_id: str) -> Member | None:
        return next((m for m in self.members if m.sender_id == sender_id), None)


def _plain_text(prop: dict | None, key: str) -> str:
    return "".join(part.get("plain_text", "") for part in (prop or {}).get(key) or []).strip()


def load_roster(client: NotionClient, database_id: str) -> Roster:
    """讀取啟用中的人員；缺 LINE userId、userId 重複的人分開列出，不納入 members"""
    data_source_id = client.data_source_id(database_id)
    query = {"filter": {"property": "啟用", "checkbox": {"equals": True}}}
    rows = []
    for page in client.query_all(data_source_id, query):
        props = page.get("properties") or {}
        rows.append(
            Member(
                page_id=normalize_page_id(page["id"]),
                name=_plain_text(props.get("姓名"), "title"),
                sender_id=_plain_text(props.get("LINE userId"), "rich_text"),
            )
        )
    counts: dict[str, int] = {}
    for row in rows:
        if row.sender_id:
            counts[row.sender_id] = counts.get(row.sender_id, 0) + 1
    return Roster(
        members=tuple(r for r in rows if r.sender_id and counts[r.sender_id] == 1),
        no_line_user_id=tuple(r.name for r in rows if not r.sender_id),
        duplicated=tuple(r.name for r in rows if r.sender_id and counts[r.sender_id] > 1),
    )


# ------------------------------------------------------------ MessageCenter


def fetch_daily_reports(
    url: str, api_key: str, report_date: str, sender_ids: list[str], http: Http = urllib_http
) -> dict:
    """呼叫 MessageCenter（stateless）的 get_daily_reports，回傳其 structuredContent"""
    if not api_key:
        raise ToolError("MC_API_KEY is not set for daily_report_writer.")
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "get_daily_reports",
            "arguments": {"report_date": report_date, "sender_ids": list(sender_ids)},
        },
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "X-API-Key": api_key,
    }
    status, data = http("POST", url, headers, payload)
    if status >= 400 or not isinstance(data, dict) or "result" not in data:
        raise ToolError(f"MessageCenter get_daily_reports failed ({status}): {data}")
    result = data["result"]
    if result.get("isError"):
        raise ToolError(f"MessageCenter get_daily_reports error: {result.get('content')}")
    content = result.get("structuredContent")
    if content is None:
        content = json.loads(result["content"][0]["text"])
    return content


# -------------------------------------------------------------------- tool


# 日報段落的關鍵字（依名單順序回報）。只要偵測到任一個，skill 就必須當成日報處理，
# 避免地端模型被前後閒聊干擾而誤判「非日報」（smartcic/docs/known-issues.md 第 14 項）
REPORT_SECTION_PATTERNS = (
    ("到廠", re.compile(r"到廠")),
    ("出廠", re.compile(r"出廠")),
    ("今日工作", re.compile(r"今日工作|今天工作|本日工作")),
    ("施工進度", re.compile(r"施工進度|工作進度")),
    ("問題與待協助", re.compile(r"問題與待協助|待協助|問題事項")),
    ("明日計畫", re.compile(r"明日(?:工作)?計[畫劃]|明天(?:工作)?計[畫劃]|明日工作")),
)


def detect_report_sections(text: str) -> list[str]:
    """原文中出現的日報段落名稱；空清單代表看不出任何日報段落"""
    return [name for name, pattern in REPORT_SECTION_PATTERNS if pattern.search(text or "")]


def clean_section(value: Any) -> str:
    """模型常把換行送成字面上的 \\n，還原成真正的換行"""
    text = "" if value is None else str(value)
    return text.replace("\\r\\n", "\n").replace("\\n", "\n").strip()


def _report_date(args: dict) -> str:
    report_date = str(args.get("report_date", "")).strip()
    try:
        date.fromisoformat(report_date)
    except ValueError as exc:
        raise ToolError(f"report_date must be YYYY-MM-DD, got {report_date!r}") from exc
    return report_date


def _clients(env: dict, http: Http) -> tuple[NotionClient, Roster]:
    client = NotionClient(env_value(env, "NOTION_TOKEN"), http)
    roster_db = required_env(env, "NOTION_ROSTER_DATABASE_ID")
    return client, load_roster(client, roster_db)


def _mc(env: dict) -> tuple[str, str]:
    api_key = env_value(env, "MC_API_KEY")
    if not api_key:
        raise ToolError("MC_API_KEY is not set for daily_report_writer.")
    return env_value(env, "MC_MCP_URL") or DEFAULT_MC_MCP_URL, api_key


def get_roster_daily_reports(args: dict, env: dict, http: Http = urllib_http) -> dict:
    report_date = _report_date(args)
    url, api_key = _mc(env)
    _, roster = _clients(env, http)

    by_sender: dict[str, dict] = {}
    timezone = ""
    if roster.members:
        sender_ids = [m.sender_id for m in roster.members]
        content = fetch_daily_reports(url, api_key, report_date, sender_ids, http)
        timezone = content.get("timezone", "")
        by_sender = {r.get("sender_id"): r for r in content.get("reports") or []}

    reports = []
    for m in roster.members:
        if m.sender_id not in by_sender:
            continue
        text = by_sender[m.sender_id].get("combined_text", "")
        reports.append(
            {
                "sender_id": m.sender_id,
                "name": m.name,
                "combined_text": text,
                "report_sections": detect_report_sections(text),
            }
        )
    return {
        "report_date": report_date,
        "timezone": timezone,
        "reports": reports,
        "missing": [m.name for m in roster.members if m.sender_id not in by_sender],
        "no_line_user_id": list(roster.no_line_user_id),
        "duplicated": list(roster.duplicated),
    }


def upsert_daily_report(args: dict, env: dict, http: Http = urllib_http) -> dict:
    report_date = _report_date(args)
    sender_id = str(args.get("sender_id", "")).strip()
    if not sender_id:
        raise ToolError("sender_id must not be empty.")
    url, api_key = _mc(env)
    client, roster = _clients(env, http)
    member = roster.find(sender_id)
    if member is None:
        raise ToolError(
            f"sender_id {sender_id} is not in the active roster (or is duplicated); "
            "nothing written. Use only sender_id values from get_roster_daily_reports."
        )

    content = fetch_daily_reports(url, api_key, report_date, [sender_id], http)
    reports = content.get("reports") or []
    if not reports:
        raise ToolError(f"No text messages from {member.name} on {report_date}; nothing written.")
    report = reports[0]

    row = DailyReportRow(
        title=f"{member.name} {report_date}",
        report_date=report_date,
        sender_id=sender_id,
        roster_page_id=member.page_id,
        raw_text=report.get("combined_text", ""),
        submitted_at=(report.get("messages") or [{}])[-1].get("sent_at"),
        **{key: clean_section(args.get(key)) for key in SECTIONS},
    )
    database_id = required_env(env, "NOTION_DAILY_REPORT_DATABASE_ID")
    result = client.upsert_daily_report(database_id, row)
    return {"title": row.title, "name": member.name, "report_date": report_date, **result}


def _str(description: str) -> dict:
    return {"type": "string", "description": description}


GET_TOOL = {
    "name": "get_roster_daily_reports",
    "description": (
        "Read the active daily-report roster from Notion and fetch every member's text "
        "messages for report_date from MessageCenter in one call. Returns reports "
        "(sender_id, name, combined_text, report_sections) for members who posted, and the name lists "
        "missing (no message), no_line_user_id and duplicated for the summary. "
        "report_sections lists the daily-report headings found in combined_text; when it is "
        "non-empty the message IS a daily report even if it also contains chat. "
        "Use these values as-is; do not add names that are not in these lists."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {"report_date": _str("Date in YYYY-MM-DD (Asia/Taipei).")},
        "required": ["report_date"],
    },
    "annotations": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
}

UPSERT_TOOL = {
    "name": "upsert_daily_report",
    "description": (
        "Create or update one member's daily report row in the Notion daily report database "
        "(same member + same date = one row). sender_id must come from "
        "get_roster_daily_reports; name, 人員 relation, 原文 and 提交時間 are filled "
        "automatically. Pass plain strings only, with real newlines between lines; use an "
        "empty string for a section the message does not mention. "
        "Returns action (created/updated), name, page_id, url and title."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "report_date": _str("Date in YYYY-MM-DD (Asia/Taipei)."),
            "sender_id": _str("sender_id of the member, copied from get_roster_daily_reports."),
            "arrive_time": _str("到廠, e.g. '8:30'."),
            "leave_time": _str("出廠, e.g. '17:30'."),
            "today_work": _str("今日工作."),
            "progress": _str("施工進度, one 'item percentage' per line."),
            "issues": _str("問題與待協助 ('無' if the message says 無)."),
            "tomorrow_plan": _str("明日計畫."),
        },
        "required": ["report_date", "sender_id"],
    },
    "annotations": {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
}


TOOLS = {tool["name"]: tool for tool in (GET_TOOL, UPSERT_TOOL)}
HANDLERS = {
    "get_roster_daily_reports": get_roster_daily_reports,
    "upsert_daily_report": upsert_daily_report,
}

# ---------------------------------------------------------------- JSON-RPC


def handle(message: dict, env: dict, http: Http = urllib_http) -> dict | None:
    """處理一則 JSON-RPC 訊息；notification 回傳 None"""
    method = message.get("method")
    msg_id = message.get("id")
    if msg_id is None:  # notification（例如 notifications/initialized）
        return None

    def ok(result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    if method == "initialize":
        params = message.get("params") or {}
        return ok(
            {
                "protocolVersion": params.get("protocolVersion") or DEFAULT_PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }
        )
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": list(TOOLS.values())})
    if method == "tools/call":
        params = message.get("params") or {}
        handler = HANDLERS.get(params.get("name"))
        if handler is None:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32602, "message": f"Unknown tool: {params.get('name')}"},
            }
        arguments = params.get("arguments") or {}
        if isinstance(arguments, str):  # 地端模型偶爾把物件包成字串
            try:
                arguments = json.loads(arguments)
            except ValueError:
                arguments = {}
        try:
            data = handler(arguments, env, http)
        except ToolError as exc:
            return ok({"content": [{"type": "text", "text": str(exc)}], "isError": True})
        text = json.dumps(data, ensure_ascii=False)
        return ok({"content": [{"type": "text", "text": text}], "structuredContent": data})
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> int:
    env = dict(os.environ)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            try:
                reply = handle(message, env)
            except Exception as exc:  # 不讓單一請求打掛 server
                print(f"[{SERVER_NAME}] {exc!r}", file=sys.stderr)
                reply = {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "error": {"code": -32603, "message": str(exc)},
                }
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
