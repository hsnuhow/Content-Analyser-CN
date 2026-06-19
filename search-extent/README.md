# search-extent — 搜尋情報層（Search Intelligence）

第 4 個 Cloud Run 微服務。**模組章程（2026-06-19 重定義）**。

## 一句話目的

**在「爬取之前」，針對一個主題/關鍵字，提供它的搜尋面貌——「需求（大家在搜什麼）」與「供給（現在什麼內容在贏）」——讓使用者決定該爬什麼，並供報告做延伸/缺口分析。**

管線定位 = 第 0 階（前置情報）：

```
[search-extent 搜尋情報] ─(URL 清單)→ 草稿 → [content-crawler 爬取] → [analysis-pipeline 分析→報告]
                        ─(關鍵字)──────────────────────────────────→ 報告 §7 延伸/缺口
```

## 嚴格邊界（「不做」清單）

- ❌ 不爬網頁正文（那是 content-crawler）
- ❌ 不分析內容、不產報告（那是 analysis-pipeline）
- ❌ 不持久化使用者資料（**無狀態**，每次呼叫即算即回）
- ❌ 不變更任何外部帳戶、不投放廣告（Ads 唯讀）
- ❌ 只回「情報清單」（關鍵字 / URL + metadata），**絕不回文章正文**

輸入：關鍵字/種子詞 + 地區/語言。輸出：結構化清單，交由控制平面（content-analyser）接力。
**單向**——本服務不主動呼叫 crawler/analysis。各子功能 best-effort 獨立降級。

## 功能歸屬（子功能 = 各自端點 / 資料源 / 開關）

| 子功能 | 端點 | 輸入 → 輸出 | 資料源 | 啟用條件 | 狀態 |
|--------|------|------------|--------|---------|------|
| **A 需求側·關鍵字延伸** | `POST /api/expand` | 種子詞 → 關聯關鍵字 + 量級 + 競爭度 | Google Ads Keyword Planner（唯讀） | `ADS_*` 憑證齊 | ⚠️ **未完成**（卡 `ADS_DEVELOPER_TOKEN`，Basic access 待核准） |
| **B 供給側·內容發現** | `POST /api/discover` | 關鍵字 → 推薦爬取 URL（+來源類型/地區/旗標） | Vertex Gemini + Google Search grounding（系統 SA） | `GOOGLE_CLOUD_PROJECT` | ✅ **可用** |
| **C 趨勢層** | `POST /api/trends`（規劃） | 主題 → 趨勢/季節性 | BigQuery `google_trends` | — | 🔲 未做 |

> grounding 在 Google 伺服器端執行（**非本服務直爬 Google**），故無資料中心 IP / CAPTCHA 問題；
> 用 Cloud Run SA 的 ADC，不需 API key、不需建 CSE。

## API（X-API-Key，需 'expand' 權限）

| 端點 | 說明 |
|------|------|
| `GET /health` | 探活；含 `expand_configured`（A 是否就緒）、`discover_configured`（B 是否就緒） |
| `POST /api/expand` | A：`{seeds:[...], language_id?, geo_ids?, limit?}` → 關聯關鍵字（**未完成**） |
| `POST /api/discover` | B：`{query, max?:50, angles?}` → `{status, query, count, by_source, candidates:[{url,title,domain,source_type,region,flag}]}` |

`/api/discover` 範例：
```bash
curl -X POST "$URL/api/discover" -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"query":"循環扇","max":50}'
```
- `source_type`：媒體 / 社群 / 論壇 / 影音 / 電商（同 analysis-pipeline 分類）。
- `region`：TW / HK / ?（TW 優先排序）。`flag`：列表頁 / 首頁（非文章頁，前端預設不勾）。
- grounding 系統付 token → `system_token_usage`（service=search-extent，後台用量總覽可見）。

## 憑證 / 環境變數（Secret Manager 注入）

| 變數 | 子功能 | 說明 |
|------|--------|------|
| `SEARCH_EXTENT_API_KEY` | 全部 | 服務自身存取金鑰 |
| `GOOGLE_CLOUD_PROJECT` | B | Vertex grounding（**B 唯一需要**，已具備） |
| `ADS_DEVELOPER_TOKEN` 等 5 個 `ADS_*` | A | Google Ads（**待核准，A 才會啟用**；含 CLIENT_ID/SECRET/REFRESH_TOKEN/LOGIN_CUSTOMER_ID） |

A 的初次設定（Ads OAuth / refresh token / dev token）見 `gen_refresh_token.py` 檔頭。

## 狀態（2026-06-19）

- **B 內容發現**：已實作、驗證（循環扇/保時捷/CHANEL 留出驗證，TW ~98%、來源多樣、與人工挑選高度重疊）。
- **A 關鍵字延伸**：程式已寫，但 `ADS_DEVELOPER_TOKEN` 未核准 → **未完成、不啟動**；`/health` 顯示 `expand_configured:false`。
- **C 趨勢層**：未做。
