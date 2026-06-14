# search-extent

需求側情報微服務（第 4 個 Cloud Run 服務）。

種子關鍵字（來自分析的語意群 TF-IDF top 詞）→ **Google Ads Keyword Planner**
（`KeywordPlanIdeaService.GenerateKeywordIdeas`）的關聯關鍵字 + 平均搜尋量 + 競爭度。
供報告「延伸附錄」與 §7 接地之用。**唯讀，不投放、不變更帳戶。**

## API（X-API-Key，需 'expand' 權限）

| 端點 | 說明 |
|------|------|
| `GET /health` | 探活（含 `ads_configured` 是否憑證齊備）|
| `POST /api/expand` | `{seeds:[...], language_id?, geo_ids?, limit?}` → 關聯關鍵字 |

`/api/expand` 範例：
```bash
curl -X POST "$URL/api/expand" -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"seeds":["初生光采","美白精華"],"language_id":"1018","geo_ids":["2158"],"limit":100}'
```
回傳：`{status, seeds, count, ideas:[{text, avg_monthly_searches, competition, competition_index}]}`
（語言 1018＝繁體中文、地區 2158＝台灣，為預設值。）

## 憑證（環境變數，部署時由 Secret Manager 注入）

| 變數 | Secret |
|------|--------|
| `ADS_DEVELOPER_TOKEN` | ADS_DEVELOPER_TOKEN（等 Basic access 核准後建立）|
| `ADS_CLIENT_ID` | ADS_CLIENT_ID |
| `ADS_CLIENT_SECRET` | ADS_CLIENT_SECRET |
| `ADS_REFRESH_TOKEN` | ADS_REFRESH_TOKEN（跑 `gen_refresh_token.py` 後建立）|
| `ADS_LOGIN_CUSTOMER_ID` | ADS_LOGIN_CUSTOMER_ID（MCC，純數字）|
| `SEARCH_EXTENT_API_KEY` | 服務自身存取金鑰 |

選用：`ADS_CUSTOMER_ID`（查詢目標帳戶，預設用 login_customer_id）、
`ADS_LANGUAGE_ID`（預設 1018）、`ADS_GEO_IDS`（預設 2158）。

## 初次設定

1. OAuth client（Desktop）已建，client_id/secret 已存 Secret Manager。
2. 產 refresh token：見 `gen_refresh_token.py` 檔頭說明。
3. Google Ads API Basic access 核准後，建立 `ADS_DEVELOPER_TOKEN`。
4. 部署（單一服務）後設 `SEARCH_EXTENT_API_KEY` 與上述 secrets。

## 後續（規劃中）

- BigQuery `google_trends`（趨勢層）、Gemini Search grounding（PAA 式問句）。
- 接入 analysis-pipeline 報告「延伸附錄」與 §7 缺口偵測。
