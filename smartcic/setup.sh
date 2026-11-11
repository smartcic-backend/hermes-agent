#!/usr/bin/env bash
# 部署 smartcic 日報排程（可重複執行）：
#   1. 把 cron 前置檢查 precheck.py 複製到 $HERMES_HOME/scripts/
#   2. 建立 cron job「pm-daily-report」（已存在就略過；要修改請用 hermes cron edit）
#
# skills 與 MCP server 不需要複製：config.yaml 依 smartcic/config.example.yaml 設定後，
# 直接從 repo 讀取。
#
# 用法：
#   本機   HERMES_HOME=$PWD/.hermes-data smartcic/setup.sh
#   Docker docker exec hermes /opt/smartcic/setup.sh
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
: "${HERMES_HOME:?請先設定 HERMES_HOME（例如 HERMES_HOME=\$PWD/.hermes-data）}"
hermes_cmd="${HERMES_CMD:-hermes}"

mkdir -p "$HERMES_HOME/scripts"
cp "$here/scripts/precheck.py" "$HERMES_HOME/scripts/precheck.py"
echo "已複製 precheck.py → $HERMES_HOME/scripts/"

job="pm-daily-report"
if "$hermes_cmd" cron list 2>/dev/null | grep -q -- "$job"; then
  echo "cron job $job 已存在，略過"
else
  # 週二～週六 08:30 上傳「前一天」的日報：成員多在 19:00–22:30 才交日報。
  # deliver 先用 local，確認輸出正確後再改成實際通道。
  "$hermes_cmd" cron create "30 8 * * 2-6" \
    "依 daily-report skill 上傳昨天的日報到 Notion，不要詢問確認，直接執行到底。" \
    --name "$job" \
    --script precheck.py \
    --skill daily-report \
    --timezone Asia/Taipei \
    --deliver local
  echo "已建立 cron job $job"
fi
