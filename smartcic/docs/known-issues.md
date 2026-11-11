# 已知問題與排查紀錄

日報上傳（cron `pm-daily-report` / `daily-report` skill）實際遇到的問題、原因與處置。
遇到新問題時照同樣格式補上，讓下一個人不用重新查一次。
第 1–8 項移植自 digital-employee（當時部署在 `~/.hermes`）；第 9 項起是搬進 hermes-agent `smartcic/` 之後遇到的。

狀態：✅ 已修正 · ⚠️ 未解決，有暫時做法 · 📝 注意事項

---

## 1. ✅ 排程比交日報早，每天都判定全員「未交」（2026-11-06）

**症狀**：cron 每天顯示 `ok`，摘要卻是全員「未交」，Notion 幾乎沒有資料。

**原因**：原本平日 18:00 上傳**當天**的日報，但成員都在 19:00–22:30 才交，有人隔天早上才補交。

**處置**：改成**週二到週六 08:30 上傳前一天**的日報（`smartcic/setup.sh` 的 `30 8 * * 2-6`；`smartcic/scripts/precheck.py` 的報告日 = 執行日 − 1，週末不喚醒）。

**教訓**：排程時間要依**實際資料什麼時候到**來決定。

---

## 2. ✅ 地端模型（qwen3.6:35b）組 Notion 巢狀 JSON 會壞（2026-11-06）

**症狀**：`tool_call 'calls' is not valid JSON`，接著模型開始亂猜屬性名稱，最後沒寫入也沒回報失敗；偶爾寫成功，換行也被存成字面上的 `\n`。

**處置**：寫入一律走 `smartcic/mcp/daily_report_writer.py` 的 `upsert_daily_report`，參數全是扁平字串，字面 `\n` 會還原成換行；同人同日 0 筆新增、1 筆更新、2 筆以上報錯不動資料。Notion MCP 不開放 `API-post-page`。

**原則**：不要讓地端模型手組多層巢狀的 API payload。

**教訓**：cron 狀態 `ok` **不代表已經上傳**，要看摘要表與「新增／更新」人數。

---

## 3. ⚠️ 補交或標錯日期的日報會被歸到錯的那一天

**原因**：用**訊息送出那天**（台北時間）分日期，不看內文。例如 11/04 22:33 送出、內文寫「11/3」的日報被算到 11/04。

**暫時做法**：補傳時人工確認。另見第 15 項。

---

## 4. 📝 DB 時間是 UTC，判斷是哪一天要換成台北時間

`messageroute_received_message.sent_at` 存 UTC。查「某天」要寫：
```sql
WHERE (sent_at AT TIME ZONE 'Asia/Taipei')::date = '2026-11-06'
```

---

## 5. ✅ repo 搬移或改名後，skills.external_dirs 留著失效的路徑

`config.yaml` 裡的路徑不會自動更新。搬移 repo 後要手動改 `skills.external_dirs` 與 `mcp_servers.daily_report_writer.args`（見第 12 項）。

---

## 6. ⚠️ 連不到地端 Ollama，排程直接失敗（2026-11-07）

**症狀**：輸出 `RuntimeError: Connection to the provider timed out before it was established`，沒有摘要表。

**原因**：`http://<OLLAMA_HOST>:11434` 逾時（主機沒開或內網、VPN 不通）。

**處置**：`curl -m 5 http://<OLLAMA_HOST>:11434/api/tags` 確認；恢復後手動補傳：
`hermes --profile <p> chat -Q --oneshot -s daily-report -q "上傳 YYYY-MM-DD 的日報"`。
待辦：用 cron 的 `--failure-deliver` 讓失敗時通知人。

---

## 7. ⚠️ Notion 免費方案的 block 額度用完，新增頁面回 403（2026-11-09）

**症狀**：`Notion POST /pages failed (403): This workspace has used all of its free blocks.`；更新（PATCH）仍可以。

**處置**：升級方案或刪除內容釋出 block，再補傳那幾天（重跑不會產生重複列）。

---

## 8. ✅ 地端模型抄錯 page id、編造名單上沒有的人（2026-11-09）

**症狀**：relation id 少一個 `-` 導致寫入失敗；「未交」出現名單上沒有的人。

**處置**：`get_roster_daily_reports` 由程式讀名單並直接回傳各種姓名清單；`upsert_daily_report` 只收 sender_id，由程式反查姓名與 relation，不在名單上就拒絕；page id 一律經過 `normalize_page_id()`。

**原則**：給地端模型的工具不要讓它搬運 id。

---

## 9. ✅ MessageCenter `/mcp` 回 404（2026-11-11）

**症狀**：`curl -X POST <MC>/mcp` 回 404。本機是 Django 的 HTML「Page not found at /mcp」；遠端主機（nginx → gunicorn）是 JSON 的 404。

**原因**：`/mcp` 不是 Django URL，而是 ASGI 層的 `with_mcp()`（MessageCenter `config/routing.py`、`apps/mcp_server/asgi.py`）攔截的路徑。用 `manage.py runserver` 或 gunicorn + `config.wsgi`（正式機目前的部署方式）啟動時，請求不會經過它。

**處置**：本機用 uvicorn 啟動 `config.routing:application`（VS Code launch「Django ASGI (uvicorn + MCP)」）。不帶 key 打 `/mcp` 回 **401** 就代表端點存在。遠端測試站改用 ASGI 後，`https://<測試站網域>:<port>/mcp` 2026-11-11 起已提供 `/mcp`。token 是遠端 DB 的，和本機不同：`docker exec messagecenter python manage.py report_token --action list`。排查時注意：ASGI 的 daphne 打 `/mcp` 卻拿到 Django 的 404 頁，代表執行中的程式還是舊版（程式碼更新後沒重啟）。

---

## 10. ✅ Hermes 連 MessageCenter 回 401 Invalid or expired API key（2026-11-11）

**原因**：`.env` 的 `MC_API_KEY` 填的是測試字串。

**處置**：用 `poetry run python manage.py report_token --action create --exp YYYY-MM-DD` 產生 token，先用 curl 驗證再寫進 `.env`：
```bash
curl -s -X POST http://127.0.0.1:8000/mcp -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' -H 'X-API-Key: <token>' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```
多 profile 時每個 profile 的 `.env` 都要設（`hermes --profile <p> config set ...`）。

---

## 11. ✅ 連 Notion 出現 SSL CERTIFICATE_VERIFY_FAILED（2026-11-11）

**原因**：終端機的 `python3` 是 python.org 版 3.9，安裝後沒跑「Install Certificates.command」，CA 憑證檔是空的。

**處置**：`daily_report_writer.py` 的 `build_ssl_context()`：預設 trust store 沒有 CA 時改用系統 CA bundle（macOS `/etc/ssl/cert.pem`、Debian `/etc/ssl/certs/ca-certificates.crt`）。有設 `SSL_CERT_FILE` 時以它為準。

---

## 12. ✅ 多 profile 時 MCP 腳本 can't open file（2026-11-11）

**症狀**：`mcp-stderr.log` 出現 `can't open file '.hermes-data/../../../smartcic/mcp/daily_report_writer.py'`。

**原因**：gateway 執行時，`mcp_servers` 裡的 `${HERMES_HOME}` **永遠是根資料目錄**（`.hermes-data`），不是 profile 目錄。但 `skills.external_dirs` 的相對路徑是以 **profile 目錄**為基準。`hermes --profile <p> mcp test` 又會把 `HERMES_HOME` 設成 profile 目錄，所以測試通過、實際執行卻失敗。

**處置**：非 default profile 的 `daily_report_writer.args` 填**絕對路徑**；`external_dirs` 用 `../../../smartcic/skills`。

**教訓**：`hermes mcp test` 通過不代表 gateway 裡也能啟動，要看 `logs/mcp-stderr.log`。

---

## 13. ✅ 設定修好了，模型仍說找不到 `mcp__daily_report_writer__*`（2026-11-11）

**原因**：`hermes chat` 會交給**執行中的 gateway** 處理（log：`gateway.run: inbound message ... platform=local`）。gateway 啟動 MCP 失敗過一次後，不會因為 config 修好就重新載入。

**處置**：改過 MCP 設定或 writer 程式，一定要重啟 gateway（前景執行就 Ctrl+C 後重開；沒有 gateway 時 CLI 會以 `--idle-exit` 自動帶起一個）。

---

## 14. ✅ 有完整日報卻被判成「非日報」，摘要還寫「已錄入」（2026-11-11）

**症狀**：補跑 11/10 時，某位成員的訊息是「三句閒聊 + 完整五段日報」，模型歸成「有訊息但非日報」沒寫入；摘要說明卻寫「已將欄位錄入」。Notion 實際只有 3 列。

**原因**：地端模型被前面的閒聊干擾而誤判；摘要也沒有照 `upsert_daily_report` 的實際結果寫。

**處置**：
- `get_roster_daily_reports` 每筆回傳 `report_sections`：由程式偵測原文中的日報段落（`detect_report_sections()`）。
- skill 0.5.0：`report_sections` 非空就一定是日報，必須寫入；摘要的「新增／更新」只能列 upsert 實際成功的人。

**教訓**：每次上傳後要**讀回 Notion 核對列數**，不要只看摘要。能由程式判斷的事，不要交給地端模型。

---

## 15. 📝 一筆原文同時有前一天的內容（2026-11-11）

某位成員 11/10 的原文同時含 11/9 與 11/10 的日報。這次模型只取 11/10 的部分，但不保證每次都會這樣做。屬於第 3 項的變形，補傳時要人工確認。

---

## 16. 📝 施工進度寫「無」會存成空白（2026-11-11）

skill 規則只要求「問題與待協助」保留原文的「無」，其他段落寫「無」時可能存成空字串。要統一保留的話，修改 skill 步驟 3 的第 3 條。

---

## 排查順序（日報沒進 Notion 時）

1. **看摘要與輸出**：cron 輸出 `ls -t $HERMES_HOME/cron/output/<job_id>/`，看有沒有摘要表、數字對不對
2. **讀回 Notion**：查日報資料庫該日期的列數，和摘要的「新增＋更新」比對（見第 14 項）
3. **gateway 有沒有在跑、是否已重啟**：`pgrep -fl "gateway run"`；改過設定就重啟（見第 13 項）
4. **gateway 裡的 MCP**：`logs/agent.log`、`profiles/<p>/logs/agent.log` 搜尋 `MCP: registered`、`Failed to connect`；`logs/mcp-stderr.log` 搜尋 `can't open file`（見第 12 項）
5. **連線**：`hermes --profile <p> mcp test messagecenter|daily_report_writer|notion`；MC 用 curl 打 `/mcp`（見第 9、10 項）；模型 `curl http://<OLLAMA_HOST>:11434/api/tags`（見第 6 項）
6. **資料**：MessageCenter 用台北時間查那天的訊息（見第 4 項）；Notion 名單的 `LINE userId` 有沒有填、有沒有重複
7. **寫入錯誤**：export 那次 session，找 `upsert_daily_report` 回傳的錯誤（見第 2、7 項）
