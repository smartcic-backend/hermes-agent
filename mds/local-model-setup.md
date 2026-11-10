# 地端模型設定（Ollama）

讓 hermes 使用公司內網的 Ollama 模型，設定與 digital-employee 專案相同。

| 項目 | 值 |
|---|---|
| Provider | `custom`（OpenAI 相容 API） |
| 位址 | `http://<OLLAMA_HOST>:11434/v1` |
| 模型 | `qwen3.6:35b` |
| Context 長度 | `65536` |

> `<OLLAMA_HOST>` 是公司內網的 Ollama 主機位址，請向管理者取得。
> 這個 repo 是公開的，**不要把真實內網位址寫進會 commit 的檔案**；它只會存在 `.hermes-data/config.yaml`（不 commit）。

---

## 1. 確認連得到 Ollama

```bash
curl -m 5 http://<OLLAMA_HOST>:11434/api/tags
```

成功會回傳 JSON，列出主機上有的模型。只看名稱：

```bash
curl -s -m 5 http://<OLLAMA_HOST>:11434/api/tags \
  | python3 -c "import sys,json; print([m['name'] for m in json.load(sys.stdin)['models']])"
```

確認清單裡有 `qwen3.6:35b`。

Docker 版還要確認**容器裡**也連得到：

```bash
docker exec hermes sh -c 'curl -s -m 5 -o /dev/null -w "%{http_code}\n" http://<OLLAMA_HOST>:11434/api/tags'
# 應印出 200
```

連不到：確認人在公司網路或已連 VPN，以及 Ollama 主機有開。

## 2. 寫入設定

用 hermes 自己的指令寫入（只改這幾個鍵，不會整份覆蓋 `config.yaml`）。

**本機版：**

```bash
hermes config set model.provider custom
hermes config set model.base_url http://<OLLAMA_HOST>:11434/v1
hermes config set model.default qwen3.6:35b
hermes config set model.context_length 65536
```

**Docker 版**（在執行中的容器裡跑，寫的就是 `.hermes-data/config.yaml`）：

```bash
docker exec hermes hermes config set model.provider custom
docker exec hermes hermes config set model.base_url http://<OLLAMA_HOST>:11434/v1
docker exec hermes hermes config set model.default qwen3.6:35b
docker exec hermes hermes config set model.context_length 65536
```

每行應印出 `✓ Set model.xxx = ... in .../config.yaml`。

寫入後 `.hermes-data/config.yaml` 會有：

```yaml
model:
  provider: custom
  base_url: http://<OLLAMA_HOST>:11434/v1
  default: qwen3.6:35b
  context_length: 65536
```

### 為什麼要設 `context_length`
內網位址會讓 hermes 自動偵測模型的 context 長度。`qwen3.6` 的 metadata 宣稱 262144，
不限制的話 Ollama 會依此配置過多 VRAM，速度變慢甚至載入失敗。限制在 65536 較穩定。

## 3. 重啟並測試

```bash
# 本機版
hermes gateway restart        # 有在跑 gateway 才需要
hermes chat -q "用一句話自我介紹"

# Docker 版
docker restart hermes
docker exec hermes hermes chat -q "用一句話自我介紹"
```

第一次呼叫若模型還沒載入，Ollama 需要一點時間把模型載進記憶體，回應會比較慢。

## 換成其他模型

先用步驟 1 的指令列出可用的模型名稱，再改 `model.default`：

```bash
hermes config set model.default <模型名稱>     # Docker 版前面加 docker exec hermes
```

不同模型的建議 `context_length` 不同；記憶體不夠時調低這個值。

## 疑難排解

| 現象 | 原因 / 處理 |
|---|---|
| `Connection to the provider timed out before it was established` | 連不到 Ollama：主機沒開、不在內網或 VPN 斷了。用步驟 1 的 `curl` 確認，恢復後再試 |
| `Gateway inaccessible: unsafe_control_permissions ... gateway.sock` | Mac 的 Docker Desktop 掛載問題，image 不是用本 fork 最新程式碼 build 的。重新 build：見 [mac-local-deploy.md](mac-local-deploy.md#附錄mac-上用-docker-的差異) |
| 回應很慢或逾時 | 模型冷啟動載入中，或 `context_length` 太大。可先在 Ollama 主機上確認模型狀態：`curl http://<OLLAMA_HOST>:11434/api/ps` |
| 模型名稱錯誤 | 名稱要和 `/api/tags` 列出的完全一致（含 `:35b` 這類標籤） |
