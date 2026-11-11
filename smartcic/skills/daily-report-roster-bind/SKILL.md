---
name: daily-report-roster-bind
description: "把 Notion 應交日報名單上的人員對應到 LINE userId：列出近期在 LINE 發言的人員，比對名單中尚未填 LINE userId 的人員，經使用者確認後寫回名單。觸發語：綁定日報名單、查 LINE userId、名單缺少 LINE userId、誰還沒對應 LINE、列出最近發言的人。"
version: 0.1.0
author: pm-digital-employee
platforms: [linux, macos]
metadata:
  hermes:
    tags: [PM, Report, Notion, MessageCenter, LINE]
    related_skills: [daily-report]
---

# daily-report-roster-bind

移植自 rag-mcp-agent-framework 的同名 skill。

```
步驟 1：Notion 取得名單中「LINE userId 為空」的人員
步驟 2：MessageCenter 列出近期發言者
步驟 3：提出對應建議，等待使用者確認
步驟 4：只把使用者確認過的對應寫回 Notion
```

> 🔴 本 Skill **只更新名單上既有的列，不新增名單列**。要加人請使用者直接在 Notion 名單新增，再回來綁定。
> 🔴 本 Skill 需要使用者確認，**不可在 cron 中執行**。

## 設定

| 項目 | 值 |
|------|-----|
| 應交日報名單 | Notion 上標題為「應交日報名單」的資料庫（步驟 1 用搜尋找出，不寫死 id） |

## 工具

| 用途 | 工具 |
|------|------|
| 依標題找出名單的 data source | `mcp__notion__API_post_search` |
| 查詢 Notion 資料庫內容 | `mcp__notion__API_query_data_source` |
| 更新 Notion 頁面屬性 | `mcp__notion__API_patch_page` |
| 列出近期發言者 | `mcp__messagecenter__list_recent_senders` |

`filter`、`properties` 必須以 JSON 物件傳入，不可包成字串。找不到工具時停止並回報，請使用者確認 MCP 連線。

## 步驟 1：取得未對應人員

1. `mcp__notion__API_post_search`：`{"query": "應交日報名單", "filter": {"property": "object", "value": "data_source"}}`，從結果中取**標題完全等於「應交日報名單」**的那一筆 `id`（搜尋也會回傳「日報」等相近標題，不可取錯）。找不到或有多筆完全相同時，停止並請使用者確認。
2. `mcp__notion__API_query_data_source`：

```json
{
  "filter": {
    "and": [
      { "property": "啟用", "checkbox": { "equals": true } },
      { "property": "LINE userId", "rich_text": { "is_empty": true } }
    ]
  }
}
```

3. 結果為空 → 回報「名單上所有人員都已有 LINE userId」並結束。

## 步驟 2：列出近期發言者

`mcp__messagecenter__list_recent_senders`，預設 `days: 7`；使用者指定天數（最多 31）或群組時照指定。回傳 `sender_id`、`display_name`（LINE 暱稱）、`group_id`、`last_sent_at`、`message_count`。

## 步驟 3：提出對應建議

以表格列出建議，**暱稱相似只是建議，不可自行認定**：

```
| 名單姓名 | 建議的 LINE 暱稱 | LINE userId | 最後發言 |
|----------|------------------|-------------|----------|
| 王小明 | 小明-機電 | U1234... | 10/16 18:02 |
| 李大華 | （找不到相近暱稱） | — | — |
```

再列出所有近期發言者供挑選，並詢問：「請確認哪些對應正確，或告訴我正確的對應。」

**等待使用者確認後，才進入步驟 4。**

## 步驟 4：寫回名單

寫入前先查整份名單（**不加「啟用」條件**），確認這個 LINE userId 沒有被其他列使用：

```json
{
  "filter": { "property": "LINE userId", "rich_text": { "equals": "U1234..." } },
  "page_size": 10
}
```

已被其他列使用 → 不寫入，列出那一列的姓名與啟用狀態，請使用者決定。

沒有衝突時，每位確認過的人員呼叫一次 `mcp__notion__API_patch_page`：

```json
{
  "page_id": "<名單該人員的頁面 id>",
  "properties": {
    "LINE userId": { "rich_text": [{ "text": { "content": "U1234..." } }] }
  }
}
```

完成後列出已寫入與仍未對應的人員。
