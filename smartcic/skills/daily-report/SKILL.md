---
name: daily-report
description: "收集成員當日日報並上傳到 Notion 日報表：工具一次取得應交日報名單與當日 LINE 訊息，拆成五段後寫入 Notion 日報資料庫（同人同日已存在就更新）。觸發語：上傳今天的日報、整理日報到 Notion、同步日報、上傳 10/16 的日報、把昨天的日報重新上傳。"
version: 0.5.0
author: pm-digital-employee
platforms: [linux, macos]
metadata:
  hermes:
    tags: [PM, Report, Daily, Notion, MessageCenter]
    related_skills: [daily-report-roster-bind]
---

# daily-report（日報上傳）

移植自 rag-mcp-agent-framework 的 `daily-report-upload`。依固定步驟執行，**不可省略、不可調換順序**：

```
步驟 1：決定日報日期
步驟 2：取得名單與當日日報原文（一次呼叫）
步驟 3：逐人拆解五段內容
步驟 4：逐人寫入 Notion（工具自動判斷新增或更新）
步驟 5：輸出執行摘要
```

重複執行是安全的：同一人同一天只會有一列，重跑會以最新訊息覆蓋該列內容。

由 cron（`pm-daily-report`）觸發時沒有人可以回答問題：**不要詢問確認，直接依本流程執行到底**。

> 🔴 **只能使用下方「工具」表列出的 MCP 工具。** 禁止撰寫或執行腳本、讀寫檔案、使用 terminal、
> 自行呼叫 Notion API 或尋找 token。工具呼叫失敗時依「失敗處置」回報，不要另找替代方法。

---

## 設定

| 項目 | 值 |
|------|-----|
| 應交日報名單、日報資料庫 | 由環境變數 `NOTION_ROSTER_DATABASE_ID`、`NOTION_DAILY_REPORT_DATABASE_ID` 設定，工具內部使用，模型不需要知道 |
| 時區 | Asia/Taipei |

## 工具

| 用途 | 工具 |
|------|------|
| 讀取應交日報名單並取得全員當日訊息（唯讀） | `mcp__daily_report_writer__get_roster_daily_reports` |
| 寫入一人一天的日報到 Notion（有就更新、沒有就新增） | `mcp__daily_report_writer__upsert_daily_report` |

參數 schema 以工具定義為準，所有參數都是**一般字串**，不可包成 JSON 字串。

> 名單、姓名、Notion page id、LINE userId 都由工具處理。**不要**自己查 Notion、
> **不要**傳姓名或 page id，也**不要**改用 `mcp__notion__*` 工具寫日報。

### 失敗處置

> ⚠️ **寫入 Notion 的內容必須直接來自 get_roster_daily_reports 回傳的原文，禁止自行推估或捏造任何進度、百分比、時間、人名。**

| 情況 | 處置方式 |
|------|---------|
| 找不到上表的工具 | 停止執行，回報：「目前無法載入 ○○ 工具，請確認 MCP 連線（hermes mcp test daily_report_writer）。」 |
| 步驟 2 失敗 | 停止執行，不寫入任何資料，回報失敗原因 |
| 步驟 4 某一人寫入失敗 | 記錄該人失敗原因，**繼續處理下一人**，最後在摘要列出 |

---

## 步驟 1：決定日報日期

- 沒有指定日期 → 使用台北時間的**今天**。
- 指定「昨天」、「10/16」等 → 換算成 `YYYY-MM-DD`（年份未說明時用今年）。
- 開始前先說明：「將上傳 YYYY-MM-DD 的日報。」

## 步驟 2：取得名單與當日日報原文

呼叫一次 `mcp__daily_report_writer__get_roster_daily_reports`：

```json
{ "report_date": "2026-10-16" }
```

| 欄位 | 說明 |
|------|------|
| `reports[].sender_id` | 步驟 4 要原樣傳回的 id |
| `reports[].name` | 姓名（摘要用） |
| `reports[].combined_text` | 當天所有訊息合併後的原文 |
| `reports[].report_sections` | 程式從原文偵測到的日報段落（例如 `["到廠", "今日工作"]`）；非空 = 一定是日報 |
| `missing[]` | 名單上當天沒有任何文字訊息的姓名 → 摘要「未交」 |
| `no_line_user_id[]` | 名單缺少 LINE userId 的姓名 → 摘要「名單缺少 LINE userId」 |
| `duplicated[]` | LINE userId 重複的姓名 → 摘要「名單重複」 |

`reports` 和 `missing` 都是空的 → 不需要步驟 3、4，直接輸出步驟 5 的摘要（含 `no_line_user_id`、`duplicated`）後結束。

## 步驟 3：拆解五段內容

對每一筆 `reports[]`，只根據 `combined_text` 拆出：

```json
{
  "is_report": true,
  "到廠": "8:30",
  "出廠": "17:30",
  "今日工作": "設定監視器\nA棟、B棟 現勘",
  "施工進度": "C棟停車場 60%\nD棟1F＆2F 調鏡頭 97%",
  "問題與待協助": "無",
  "明日計畫": "攝影機無法錄影查修"
}
```

1. 段落可能用「1.」「一、」「【今日工作】」或沒有編號，依語意判斷歸屬。
2. **找不到的段落填空字串 `""`，禁止推測或補寫。**
3. 問題欄原文寫「無」就填「無」；完全沒提到才填 `""`。
4. 施工進度每個項目一行，格式「項目 百分比」，項目名稱照原文。
5. 同一段落出現兩次（例如更正），以**後面**出現的為準。
6. 與日報無關的閒聊不要放進五段欄位。前後夾雜的閒聊、提問一律忽略，不影響判斷。
7. **`report_sections` 非空 → 一定是 `is_report: true`，必須執行步驟 4 寫入**，不可自行改判為非日報。
8. 只有 `report_sections` 是空清單，而且原文也看不出任何日報內容（例如只有討論、提問）時，才是 `is_report: false`，記入摘要「有訊息但非日報」，不寫入 Notion。

## 步驟 4：寫入 Notion

**逐人**呼叫一次 `mcp__daily_report_writer__upsert_daily_report`（`is_report: false` 的人不要呼叫）：

```json
{
  "report_date": "2026-10-16",
  "sender_id": "<步驟 2 的 reports[].sender_id，原樣複製>",
  "arrive_time": "8:30",
  "leave_time": "17:30",
  "today_work": "設定監視器\nA棟、B棟 現勘",
  "progress": "C棟停車場 60%\nD棟1F＆2F 調鏡頭 97%",
  "issues": "無",
  "tomorrow_plan": "攝影機無法錄影查修"
}
```

| 參數 | 對應步驟 3 的段落 |
|------|------|
| `arrive_time` / `leave_time` | 到廠 / 出廠 |
| `today_work` | 今日工作 |
| `progress` | 施工進度 |
| `issues` | 問題與待協助 |
| `tomorrow_plan` | 明日計畫 |

- 找不到的段落傳 `""`。
- 標題、姓名、「人員」關聯、原文、提交時間都由工具自動處理。
- 回傳 `action: "created"` 記入「新增」，`"updated"` 記入「更新」，人員用回傳的 `name`。
- 回傳錯誤 → 記入「寫入失敗」並附上原因，**繼續處理下一人**。

## 步驟 5：輸出執行摘要

```
YYYY-MM-DD 日報上傳結果

| 結果 | 人數 | 人員 |
|------|------|------|
| 新增 | 3 | 王小明、李大華、陳小美 |
| 更新 | 1 | 張志明 |
| 未交 | 2 | 林阿德、黃美玲 |
| 有訊息但非日報 | 1 | 吳家豪 |
| 名單缺少 LINE userId | 1 | 趙小龍 |
| 名單重複 | 0 | — |
| 寫入失敗 | 0 | — |
```

- 「未交」「名單缺少 LINE userId」「名單重複」**照抄**步驟 2 回傳的 `missing`、`no_line_user_id`、`duplicated`，不可增減。
- 「新增」「更新」「寫入失敗」「有訊息但非日報」只能是步驟 2 `reports[]` 裡的人。
- **摘要中不可出現任何工具沒有回傳過的姓名。**
- 「新增」「更新」只能列出 `upsert_daily_report` 實際回傳成功的人；沒有呼叫 upsert 的人不可描述為「已錄入」或「已寫入」。

只列示，**不發送任何提醒**。

---

## 經驗與排錯

| 情況 | 處置方式 |
|------|---------|
| `tool_call` JSON error（Expecting ',' delimiter 等） | **不要**在單一 `tool_call` block 裡放超過一個不同 tool name 的呼叫；若需呼叫多個同工具，請拆成多次 `tool_call` 依序送出。 |
| 成員的訊息包含多日內容（例如同時出現 11/9 與 11/10） | Step 3、Step 4 **只取目標日期**的段落；非目標日期的內容直接忽略，不要混合或加註來源日期。 |

