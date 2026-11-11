# smartcic：日報 skill

從 MessageCenter 取得成員在 LINE 上交的日報，拆成五段（到廠、出廠、今日工作、施工進度、問題與待協助、明日計畫）後寫入 Notion 日報資料庫。
移植自 digital-employee 的 PM 數位員工，放在本 fork 專屬的 `smartcic/` 目錄，不修改上游程式碼。

## 內容

| 路徑 | 用途 |
|---|---|
| `skills/daily-report/` | 上傳日報：讀名單和當日訊息，拆成五段，寫入 Notion（同一人同一天只有一列，重跑會覆蓋） |
| `skills/daily-report-roster-bind/` | 把 Notion 名單上的人對應到 LINE userId（需要使用者確認，不可放進 cron） |
| `mcp/daily_report_writer.py` | stdio MCP server，只用標準函式庫。模型只負責拆段，名單、page id、原文、提交時間都由程式處理 |
| `scripts/precheck.py` | cron 前置檢查：報告日是昨天；報告日是週末就不喚醒 agent |
| `config.example.yaml` | 要合併進 `config.yaml` 的 skills 與 mcp_servers 設定 |
| `env.example` | 要寫入 `.env` 的機密欄位 |
| `setup.sh` | 複製 precheck 並建立 cron job `pm-daily-report`（可重複執行） |

測試在 `tests/smartcic/`，用 `scripts/run_tests.sh --require-paths tests/smartcic` 執行。

**遇到問題先看 [docs/known-issues.md](docs/known-issues.md)**：記錄了實際踩過的坑，最後有一份排查順序。

## 前置需求

1. **MessageCenter 的 `/mcp` 要能連線。** `/mcp` 只有在 MessageCenter 以 ASGI（daphne／uvicorn）啟動時才會出現；`manage.py runserver`（WSGI）會回 404。
   檢查方式：
   ```bash
   curl -s -X POST "$MC_MCP_URL" -H 'Content-Type: application/json' \
     -H 'Accept: application/json, text/event-stream' -H "X-API-Key: $MC_API_KEY" \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
   ```
   回應應包含 `get_daily_reports` 和 `list_recent_senders`。
2. **MessageCenter API token：** `cd MessageCenter && poetry run python manage.py report_token --action create --exp YYYY-MM-DD`
3. **Notion integration token：** 這個 integration 要分享給「應交日報名單」和「日報」兩個資料庫。

## 安裝

以下以本專案的資料目錄 `.hermes-data/` 為例。

1. **設定機密**（欄位說明見 `env.example`）：
   ```bash
   export HERMES_HOME=$PWD/.hermes-data
   hermes config set NOTION_TOKEN <值>
   hermes config set MC_API_KEY <值>
   hermes config set MC_MCP_URL http://localhost:8000/mcp      # Docker 用 http://host.docker.internal:8000/mcp
   ```
2. **合併設定：** 把 `config.example.yaml` 的 `skills.external_dirs` 和 `mcp_servers` 加進 `.hermes-data/config.yaml`。已經有 `skills:` 或 `mcp_servers:` 區塊的話，合併進去，不要重複寫一個。
   用其他 profile（例如 `digital-worker-pm`）時，設定要加在 `.hermes-data/profiles/<名稱>/config.yaml`。這時 `skills.external_dirs` 以 profile 目錄為基準，要改成 `../../../smartcic/skills`。但 `daily_report_writer` 的 `args` 要填**絕對路徑**，因為 gateway 執行時 `${HERMES_HOME}` 指向根目錄 `.hermes-data`，不是 profile 目錄。機密也要另外用 `hermes --profile <名稱> config set ...` 設定。
3. **建立排程：**
   ```bash
   HERMES_HOME=$PWD/.hermes-data smartcic/setup.sh            # 本機
   docker exec hermes /opt/smartcic/setup.sh                   # Docker（容器內已設定 HERMES_HOME）
   ```
4. **重新啟動 gateway**，讓 MCP 和 skill 生效。`hermes chat` 會交給執行中的 gateway 處理，所以只要改過 MCP 設定，就一定要重啟：本機執行 `hermes gateway restart`；Docker 執行 `docker compose -f docker-compose.local.yml up -d`。第一次使用要先加上 `--build`，讓新的 volume 掛載生效。

### Docker 注意事項

- `docker-compose.local.yml` 會把 `./smartcic` 唯讀掛載到 `/opt/smartcic`，所以 `config.yaml` 裡的 `${HERMES_HOME}/../smartcic` 在本機和容器內指向同一份檔案。
- 容器裡連不到主機的 `localhost`。MessageCenter 如果跑在主機上，`MC_MCP_URL` 要用 `host.docker.internal`。
- 映像檔裡已經有 `python3` 和 `npx`（Notion MCP 要用），不需要另外安裝。

## 驗證

```bash
hermes skills list                  # 看得到 daily-report、daily-report-roster-bind
hermes mcp test daily_report_writer # 列出 get_roster_daily_reports、upsert_daily_report
hermes cron list                    # 看得到 pm-daily-report
```

接著在對話中說「上傳 2026-11-10 的日報」，確認 Notion 日報資料庫有新增或更新資料列。

## 使用

| 你說 | 會做什麼 |
|---|---|
| 上傳今天的日報、上傳 11/10 的日報 | daily-report：寫入 Notion，並輸出新增、更新、未交的摘要 |
| 綁定日報名單、誰還沒對應 LINE | daily-report-roster-bind：提出對應建議，等你確認後才寫回名單 |

Cron 每週二到週六 08:30（台北時間）自動上傳前一天的日報，結果先保留在本機（`--deliver local`）。確認輸出正確後，可以用 `hermes cron edit` 把 deliver 改成實際的通道。
