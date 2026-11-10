# Linux 用 Docker 部署

適用：Ubuntu / Debian 等 Linux 主機（實體機、VM、伺服器）。
結果：一個名為 `hermes` 的容器，裡面跑 gateway、dashboard（主機 port 8119）、webapp（主機 port 8120），資料放在專案的 `.hermes-data/`。

> 先懂三個名詞
> - **Image**：打包好的安裝檔（作業系統 + Python + 程式碼），唯讀。
> - **Container（容器）**：用 image 跑起來的實例。
> - **掛載（volume / `-v`）**：把主機的資料夾接進容器；容器刪掉，資料還在主機上。

---

## 1. 安裝 Docker

```bash
# 官方安裝腳本（Docker Engine + compose plugin）
curl -fsSL https://get.docker.com | sudo sh

# 讓目前使用者不用 sudo 就能用 docker（重新登入後生效）
sudo usermod -aG docker "$USER"
newgrp docker

# 確認
docker version
docker compose version
```

## 2. 取得程式碼

```bash
git clone https://github.com/smartcic-backend/hermes-agent.git
cd hermes-agent
```

> 一定要用本 fork 自己 build。Docker Hub 上的 `nousresearch/hermes-agent` 是上游版本，不含本 fork 的修改。

## 3. 準備資料目錄與帳密

```bash
# 資料目錄（= 容器的 /opt/data）
mkdir -p .hermes-data && chmod 700 .hermes-data

# Docker 用的 .env：容器內檔案擁有者對應到你的帳號，並設定網頁登入帳密
cat > .env <<EOF
HERMES_UID=$(id -u)
HERMES_GID=$(id -g)
HERMES_DASHBOARD_BASIC_AUTH_USERNAME=admin
HERMES_DASHBOARD_BASIC_AUTH_PASSWORD=$(openssl rand -hex 16)
EOF
chmod 600 .env

# 記下密碼，登入網頁時要用
grep BASIC_AUTH_PASSWORD .env
```

| 變數 | 意思 |
|---|---|
| `HERMES_UID` / `HERMES_GID` | 你的使用者 / 群組編號。容器會用這個身分寫檔，主機上看到的擁有者才會是你 |
| `HERMES_DASHBOARD_BASIC_AUTH_*` | dashboard 與 webapp 共用的登入帳密。網頁服務綁在非 loopback 位址時，沒有登入方式就不會啟動 |

## 4. 預先寫好 webapp 設定

```bash
cat > .hermes-data/config.yaml <<'EOF'
webapp:
  enabled: true
  host: 0.0.0.0                        # 容器內必須是 0.0.0.0，否則外面連不進來
  port: 9120                           # 容器內的 port（不用改）
  public_url: "http://localhost:8120"  # 瀏覽器實際開的網址（主機 port 8120）
EOF
```

> `port` 是容器裡 webapp 監聽的 port；`public_url` 是你在瀏覽器打的網址，登入連結和
> sign-in callback 都用它產生，必須和 `docker-compose.local.yml` 對應到的**主機 port** 一致。

> Linux 的掛載是原生檔案系統，SQLite 可以用預設的 WAL 模式。
> 只有當這份 `.hermes-data/` 也要給 Mac 上的 Docker 使用時，才需要另外加
> `database:` / `journal_mode: delete`（見 [mac-local-deploy.md](mac-local-deploy.md#附錄mac-上用-docker-的差異)）。

## 5. Build image

```bash
docker compose -f docker-compose.local.yml build
```

- 依照 `Dockerfile` 用目前目錄的程式碼做出名為 `hermes-agent` 的 image。
- 第一次會比較久（編 sqlite、裝 Python 依賴、建前端），磁碟至少預留 15GB。

## 6. 執行設定精靈（只需一次）

```bash
docker compose -f docker-compose.local.yml run --rm hermes setup
```

- `run --rm hermes setup`：用 `hermes` 服務的設定開一個一次性容器，執行 `hermes setup`，結束就刪掉容器。
- 精靈會問 API key、模型等，結果寫進 `.hermes-data/config.yaml` 與 `.hermes-data/.env`。
- 要用地端模型的話，可以先跳過模型設定，之後照 [local-model-setup.md](local-model-setup.md) 設。

## 7. 啟動

```bash
docker compose -f docker-compose.local.yml up -d
docker ps --filter name=hermes        # STATUS 應為 Up，不是 Restarting
docker logs -f hermes                 # 看 log，Ctrl+C 離開
```

`docker-compose.local.yml` 做了這些事：

| 設定 | 意思 |
|---|---|
| `volumes: ./.hermes-data:/opt/data` | 資料放在專案裡 |
| `HERMES_DASHBOARD=1` | 一起啟動 dashboard |
| `127.0.0.1:8642` | gateway API（預設未啟用，需另設 `API_SERVER_*`） |
| `127.0.0.1:8119:9119` | dashboard：主機 8119 → 容器 9119 |
| `127.0.0.1:8120:9120` | webapp：主機 8120 → 容器 9120 |
| `restart: unless-stopped` | 當機或主機重開後自動啟動 |

port 的格式是 `主機IP:主機port:容器port`。`127.0.0.1` 代表只有這台主機連得到。

### 想換 port
只要改兩個地方，容器內的 9119 / 9120 不用動：
1. `docker-compose.local.yml` 的 `ports` **左邊**的數字（主機 port）
2. `.hermes-data/config.yaml` 的 `webapp.public_url` 改成新的主機 port

改完執行 `docker compose -f docker-compose.local.yml up -d --force-recreate`（不用重新 build）。

## 8. 開啟網頁

ports 只綁 `127.0.0.1`，只有主機本身連得到。

- **在主機上**：瀏覽器開 http://localhost:8120 （webapp）、http://localhost:8119 （dashboard）
- **從自己的電腦連遠端主機**：用 SSH tunnel，不要把 ports 改成對外開放

  ```bash
  ssh -L 8119:localhost:8119 -L 8120:localhost:8120 <user>@<linux-host>
  ```
  連上後在自己電腦的瀏覽器開 http://localhost:8120 。本機端的 port 要和 `public_url` 一致，
  否則登入後的跳轉會連到錯的位址。

帳號 `admin`，密碼在專案根目錄 `.env`。

## 日常操作

```bash
docker exec -it hermes hermes                 # 在容器裡開 hermes CLI
docker exec -it hermes hermes doctor          # 健康檢查
docker exec hermes hermes chat -q "你好"      # 送一句話測試
docker restart hermes                         # 改了 .hermes-data/config.yaml 後重啟
docker compose -f docker-compose.local.yml stop    # 停止
docker compose -f docker-compose.local.yml start   # 再啟動
```

## 更新程式碼

容器裡的程式碼是 build 當下的快照，改了程式碼一定要重新 build：

```bash
git pull
docker compose -f docker-compose.local.yml build
docker compose -f docker-compose.local.yml up -d --force-recreate
```

資料在 `.hermes-data/`，重建容器不會遺失。

## 疑難排解

| 現象 | 原因 / 處理 |
|---|---|
| `docker ps` 顯示 `Restarting` | 看 `docker logs hermes` 找錯誤 |
| log 出現 `No space left on device` | Docker 磁碟滿了。`docker system df` 查看，`docker builder prune -f` 清 build 快取（安全）；仍不足再清不用的 image。**不要隨便刪 volume** |
| 網頁打不開、log 有 auth 相關錯誤 | 確認專案根 `.env` 有 `HERMES_DASHBOARD_BASIC_AUTH_USERNAME/PASSWORD` |
| `.hermes-data/` 裡的檔案主機上無法修改 | `.env` 的 `HERMES_UID/GID` 與 `id -u`/`id -g` 不一致，修正後 `up -d --force-recreate` |
| webapp 回 503 | 剛啟動還在初始化，等 10–30 秒 |
