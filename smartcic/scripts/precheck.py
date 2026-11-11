#!/usr/bin/env python3
"""cron 前置檢查（wake gate），所有員工共用（例如 pm-daily-report）。

Hermes cron 協定：stdout 最後一行若為 JSON ``{"wakeAgent": false}`` 則本次不喚醒 agent；
其他任何輸出都會喚醒，且 stdout 會被注入 agent 的 prompt。

規則：處理的是「報告日 = 執行當天（台北時間）的前一天」。成員多在 19:00–22:30 才交日報，
因此排程在隔天早上執行。報告日是週末就不喚醒。
同日重跑是安全的（daily-report skill 先查 Notion 再新增或更新），因此不需要完成標記；
agent 在排程中也不需要 terminal 權限。

只使用標準函式庫——smartcic/setup.sh 會把它複製到 $HERMES_HOME/scripts/，由 Hermes 的 Python 執行。
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")


def report_day_for(today: dt.date) -> dt.date:
    """執行日 → 報告日（前一天）。"""
    return today - dt.timedelta(days=1)


def decide(report_day: dt.date) -> tuple[bool, str]:
    """回傳 (是否喚醒, 原因)。"""
    if report_day.weekday() >= 5:
        return False, "weekend"
    return True, "due"


def main(today: dt.date | None = None) -> int:
    today = today or dt.datetime.now(TAIPEI).date()
    report_day = report_day_for(today)
    wake, reason = decide(report_day)
    if wake:
        # 這段文字會注入 agent prompt，提供上下文
        print(
            f"今天是 {today.isoformat()}（台北時間），"
            f"請依 daily-report skill 上傳 {report_day.isoformat()}（昨天）的日報。"
        )
    print(json.dumps({"wakeAgent": wake, "reason": reason}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
