"""precheck.py 的 wake gate 行為測試。

執行：pytest tests/smartcic
"""

from __future__ import annotations

import contextlib
import datetime as dt
import importlib.util
import io
import json
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "smartcic" / "scripts" / "precheck.py"
spec = importlib.util.spec_from_file_location("precheck", SCRIPT)
precheck = importlib.util.module_from_spec(spec)
spec.loader.exec_module(precheck)

# 執行日（cron 在早上跑，處理前一天）
TUESDAY = dt.date(2026, 11, 3)  # 報告日 11/02（一）
SATURDAY = dt.date(2026, 11, 7)  # 報告日 11/06（五）
SUNDAY = dt.date(2026, 11, 8)  # 報告日 11/07（六）
MONDAY = dt.date(2026, 11, 9)  # 報告日 11/08（日）


def gate(stdout: str) -> dict:
    """模擬 Hermes：取 stdout 最後一行非空白行解析 JSON。"""
    last = [ln for ln in stdout.splitlines() if ln.strip()][-1]
    return json.loads(last)


def run(today: dt.date) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert precheck.main(today=today) == 0
    return buf.getvalue()


class PrecheckTest(unittest.TestCase):
    def test_report_day_is_previous_day(self):
        self.assertEqual(precheck.report_day_for(TUESDAY), dt.date(2026, 11, 2))
        self.assertEqual(precheck.report_day_for(dt.date(2026, 1, 1)), dt.date(2025, 12, 31))

    def test_wakes_when_report_day_is_weekday(self):
        for day in (TUESDAY, SATURDAY):
            with self.subTest(day=day):
                self.assertEqual(gate(run(day)), {"wakeAgent": True, "reason": "due"})

    def test_skips_when_report_day_is_weekend(self):
        for day in (SUNDAY, MONDAY):
            with self.subTest(day=day):
                self.assertEqual(gate(run(day)), {"wakeAgent": False, "reason": "weekend"})

    def test_wake_output_mentions_report_date_for_agent(self):
        out = run(TUESDAY)
        self.assertIn("2026-11-02", out)
        self.assertIn("昨天", out)
        self.assertEqual(gate(run(SATURDAY))["wakeAgent"], True)
        self.assertIn("2026-11-06", run(SATURDAY))

    def test_skip_prints_only_gate_line(self):
        lines = [ln for ln in run(SUNDAY).splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1)
