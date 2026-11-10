# Mac 本機部署（不用 Docker）

適用：macOS（Apple Silicon / Intel），直接在 Mac 上跑 hermes。
適合開發：改了程式碼，重新執行 `hermes` 就生效，不用 build。

---

## 1. 指定資料目錄（一定要先做）

讓 hermes 把資料放在專案的 `.hermes-data/`，而不是 `~/.hermes`：

```bash
echo 'export HERMES_HOME="$HOME/<專案路徑>/hermes-agent/.hermes-data"' >> ~/.zshrc
source ~/.zshrc
mkdir -p "$HERMES_HOME" && chmod 700 "$HERMES_HOME"
echo "$HERMES_HOME"   # 確認
```

> 安裝腳本、設定精靈、背景服務都會讀 `HERMES_HOME`。沒先設定的話，資料會寫到 `~/.hermes`。

## 2. 安裝

```bash
cd <專案路徑>/hermes-agent
./setup-hermes.sh
source ~/.zshrc   # 或開新的終端機
```

安裝腳本會：
- 安裝 uv 與 Python，依 `uv.lock` 驗證後安裝依賴（第一次 1–5 分鐘）
- 從 `.env.example` 建立專案根 `.env`（已存在就保留）
- 建立 `~/.local/bin/hermes` 指令並確保它在 PATH 裡
- 把內建 skills 同步到 `$HERMES_HOME/skills/`

確認：

```bash
which hermes          # ~/.local/bin/hermes
cat ~/.local/bin/hermes   # 應指向這個專案
```

## 3. 設定

```bash
hermes setup     # 完整設定精靈（API key、模型、訊息平台…）
hermes doctor    # 檢查；顯示的路徑應在 .hermes-data
```

要用地端模型，照 [local-model-setup.md](local-model-setup.md) 設定。

## 4. 使用

```bash
hermes                 # 開 CLI 聊天
hermes chat -q "你好"  # 送一句話就結束
hermes webapp          # 開瀏覽器版（Hermes Desktop in a browser），會給一次性登入連結
hermes dashboard       # 開 dashboard
```

### webapp 的 `host` 設定
- **只在本機用**：`.hermes-data/config.yaml` 的 `webapp.host` 用 `127.0.0.1`，只有本機連得到，用 `hermes webapp` 的一次性連結登入即可。
- **與 Docker 共用同一份 config**（`host: 0.0.0.0`）：本機版也會綁在所有網路介面上，同網段的人連得到，而且沒有登入方式就不會啟動。
  請在 `.hermes-data/.env` 加上帳密（hermes 啟動時會載入 `$HERMES_HOME/.env`）：

  ```bash
  echo "HERMES_DASHBOARD_BASIC_AUTH_USERNAME=admin" >> "$HERMES_HOME/.env"
  echo "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD=$(openssl rand -hex 16)" >> "$HERMES_HOME/.env"
  ```

## 背景服務（選用）

要讓 gateway 開機自動在背景執行：

```bash
hermes gateway install   # 裝成 macOS launchd 服務
hermes gateway status
```

> 服務會記下安裝當下的 `HERMES_HOME`。之後換了資料目錄，要重新安裝服務。

## 開發時的注意事項

- 改了 Python 程式碼：重新執行 `hermes`（gateway 要 `hermes gateway restart`）。
- 跑測試一律用 `scripts/run_tests.sh`，不要直接 `pytest`。跑測試前確認 `HERMES_HOME` 已設定，否則測試工具會在 `~/.hermes` 建立工具目錄。
- 編輯器（Cursor / VS Code）建議把 `.hermes-data/` 加入排除清單，避免索引對話紀錄與資料庫。

---

## 附錄：Mac 上用 Docker 的差異

步驟和 [linux-docker-deploy.md](linux-docker-deploy.md) 相同（安裝改用 Docker Desktop），但有幾點 Mac 特有：

1. **資料庫要用 `delete` 模式**
   Docker Desktop 把 Mac 資料夾掛進容器的方式（virtiofs / `fakeowner`）不支援 SQLite 的 WAL 共享記憶體，
   本機與容器輪流使用同一個資料庫時可能損毀。在 `.hermes-data/config.yaml` 加上：

   ```yaml
   database:
     journal_mode: delete
   ```

2. **一定要用本 fork 的 image**
   這種掛載會忽略檔案權限，gateway 建在 `.hermes-data/` 的控制 socket 永遠顯示 0755，
   上游版本會拒絕連線（`Gateway inaccessible: unsafe_control_permissions ... gateway.sock`）。
   本 fork 的 `gateway/control_socket.py` 已修正：偵測到權限不對時，改把 socket 放到容器暫存目錄，
   並在 `.hermes-data/gateway.sock.path` 留下指向它的檔案。log 會看到：
   `Control socket in /opt/data is not private ... listening in a private temp directory`（正常）。

3. **不要用 host networking**
   上游的 `docker-compose.yml` 用 `network_mode: host`，Mac 需另外在 Docker Desktop 開啟。
   `docker-compose.local.yml` 改用一般的 port 對應，Mac / Linux 都能用。

4. **注意磁碟空間**
   Docker Desktop 的虛擬磁碟有上限（常見 64GB），build 快取很容易塞滿，症狀是容器一直 `Restarting`、
   log 出現 `No space left on device`。用 `docker system df` 查看、`docker builder prune -f` 清理。
   Docker Desktop → Settings → Resources 可調整上限。

5. **本機與 Docker 不要同時跑 gateway**（共用同一個 `.hermes-data/`）。

6. **port 不一樣**
   Docker 版把容器的 9119 / 9120 對應到 Mac 的 **8119 / 8120**（見 `docker-compose.local.yml`），
   所以 `.hermes-data/config.yaml` 的 `webapp.public_url` 設成 `http://localhost:8120`。
   本機版（不用 Docker）沒有 port 對應，直接監聽 config 裡的 `port`（webapp 9120、dashboard 9119）。
   兩邊共用同一份 config 時，本機版請直接開 http://localhost:9120 ；`hermes webapp` 印出的連結會用
   `public_url`（8120），在本機版會連不到，只用本機版的話把 `public_url` 改回 `http://localhost:9120`。
