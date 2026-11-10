# 部署文件

這個資料夾收錄本 fork（smartcic-backend/hermes-agent）的部署步驟。

| 文件 | 內容 |
|---|---|
| [linux-docker-deploy.md](linux-docker-deploy.md) | 在 Linux 主機用 Docker 部署 |
| [mac-local-deploy.md](mac-local-deploy.md) | 在 Mac 本機部署（不用 Docker），附 Mac 上用 Docker 的差異 |
| [local-model-setup.md](local-model-setup.md) | 設定地端 Ollama 模型 |

## 共通觀念

### 資料都放在專案的 `.hermes-data/`
Hermes 把設定、API key、對話紀錄、記憶、skills 全部放在一個「資料目錄」，由環境變數
`HERMES_HOME` 決定位置（沒設定時預設是 `~/.hermes`）。本專案統一放在專案裡：

| 執行方式 | 資料目錄 |
|---|---|
| 本機（Mac / Linux 不用 Docker） | `HERMES_HOME=<專案>/.hermes-data` |
| Docker | `./.hermes-data` 掛載到容器的 `/opt/data` |

兩種方式可以共用同一個 `.hermes-data/`。

### `.hermes-data/` 絕對不要 commit
裡面有 `.env`（API key）、對話紀錄、資料庫。它已經在 `.gitignore` 裡；專案根目錄的 `.env`
（Docker 用的帳密）也一樣不 commit。這個 repo 是公開的。

### 同一份資料不要同時跑兩個 gateway
本機的 `hermes gateway` 和 Docker 容器若共用 `.hermes-data/`，同時執行會把資料庫寫壞。
要用一邊，先停另一邊：

```bash
docker compose -f docker-compose.local.yml stop   # 停 Docker 版
hermes gateway stop                               # 停本機版
```

### 相關檔案
| 檔案 | 用途 |
|---|---|
| `docker-compose.local.yml` | 本專案的 Docker 部署設定（不含秘密，可 commit） |
| `.env`（專案根目錄） | Docker 用的 UID/GID 與網頁登入帳密（不 commit） |
| `.hermes-data/config.yaml` | Hermes 的設定（模型、webapp、資料庫模式…） |
| `.hermes-data/.env` | Hermes 的 API key 等秘密（`hermes setup` 寫入） |
