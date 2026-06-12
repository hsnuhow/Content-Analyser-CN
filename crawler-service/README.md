# Content Crawler Service（獨立爬蟲服務）

一個完全獨立、透過 API 操作的無頭瀏覽器爬蟲 Cloud Run 服務。
爬取核心嚴格對齊已驗證的 Colab v3.8（undetected-chromedriver、OneTrust/Fides 同意處理、
google-genai 選擇器輔助），並保留 Cloud Run 既有的列表頁判斷與多維度評分等加值。

---

## 1. API 規格

所有 `/api/*` 端點都必須帶上正確的 `X-API-Key` 標頭才允許存取。

### `GET /health`
健康檢查，不需金鑰。
```bash
curl https://<CRAWLER_URL>/health
# {"status":"ok","service":"content-crawler"}
```

### `POST /api/scrape`
爬取單一網址（同步），需 `X-API-Key`。

請求：
```bash
curl -X POST https://<CRAWLER_URL>/api/scrape \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <你的金鑰>" \
  -d '{
        "url": "https://www.marieclaire.com.tw/entertainment/xxxx",
        "use_gemini": false,
        "gemini_api_key": "(選填，覆蓋服務預設的 GENAI_API_KEY)"
      }'
```

成功回傳：
```json
{
  "status": "success",
  "url": "https://...",
  "title": "文章標題",
  "content": "抽取到的主文...",
  "length": 1234
}
```

其他狀態：
- `{"status":"skipped", ...}`：判定為列表/分類頁，已略過。
- `{"status":"failed", "error":"..."}`：爬取或抽取失敗。
- HTTP 401：`X-API-Key` 缺少或錯誤。
- HTTP 400：缺少 `url` 或格式不正確。

---

## 2. 環境變數

| 變數 | 用途 | 來源 |
|---|---|---|
| `CRAWLER_API_KEY` | API 存取金鑰（必填，未設定時一律回 401） | Secret Manager |
| `GENAI_API_KEY` | 服務預設的 Gemini 金鑰（選填，供低置信度時 LLM 輔助） | Secret Manager |
| `CHROME_BIN` | Chrome 執行檔路徑（Docker 內已預設 `/usr/bin/google-chrome`） | Dockerfile |
| `CHROMEDRIVER_PATH` | ChromeDriver 路徑（Docker 內已預設 `/usr/bin/chromedriver`） | Dockerfile |

---

## 3. 部署

請見專案根目錄的 `deploy.sh`，會先部署本服務（`content-crawler`），
取得其 URL 後再部署主程式並注入 `CRAWLER_SERVICE_URL` 與 `CRAWLER_API_KEY`。

手動建立存取金鑰 Secret（範例）：
```bash
# 產生一組隨機金鑰並寫入 Secret Manager
openssl rand -hex 32 | gcloud secrets create CRAWLER_API_KEY --data-file=-
# 或更新既有 secret
openssl rand -hex 32 | gcloud secrets versions add CRAWLER_API_KEY --data-file=-
```
