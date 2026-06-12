# content-crawler — 爬蟲微服務

InsightOut 的獨立爬蟲服務（Cloud Run）。輸入網址，回傳乾淨的主文內容。
可被 content-analyser、Google Colab、Claude Cowork 等透過 HTTP API 呼叫。

- **版本**：1.3.0
- **技術**：Headless Chrome + undetected-chromedriver + Gemini 輔助選取器
- **特性**：對齊 Colab v3.8（OneTrust / Fides 遮罩突破、60s 硬性時限、Dcard 自動跳過）

---

## 驗證

所有 `/api/*` 端點需 HTTP Header `X-API-Key`，需具備 **`crawl`** 權限。
驗證順序：
1. 系統金鑰（Secret Manager 注入的 `CRAWLER_API_KEY`）
2. api_keys 白名單（content-analyser 後台核發，存 Firestore `api_keys`）

---

## 端點

### GET /health
健康檢查，不需金鑰。
```json
{"status":"ok","service":"content-crawler","version":"1.3.0",
 "chrome":"Google Chrome 149...","api_key_configured":true,"firebase":"connected"}
```

### POST /api/crawl/batch（非同步，建議用於 UI / Colab 大批量）
提交批次爬取，回傳 `job_id`，背景逐一爬取，結果存 Firestore `crawl_jobs/{job_id}`。
```json
// Request（最多 100 個 URL）
{"urls": ["https://...", "https://..."], "use_gemini": false, "gemini_api_key": "..."}
// Response
{"job_id": "...", "status": "pending"}
```

### GET /api/crawl/{job_id}
查詢非同步爬取進度與結果。
```json
{"job_id":"...","status":"running|completed|failed","progress":45,
 "log":"...","results":[{"status":"success","url","title","content","length"}, ...]}
```

### POST /api/scrape（同步，單一 URL）
```json
{"url": "https://example.com/article", "use_gemini": false, "gemini_api_key": "...", "hard_timeout_sec": 60}
```

### POST /api/scrape/batch（同步，最多 20）
```json
{"urls": ["https://...", "https://..."], "use_gemini": false}
```

---

## 單篇結果格式

| status | 說明 |
|--------|------|
| `success` | `{status, url, title, content, length}`（主文在 `content` 欄位）|
| `skipped` | 列表頁或 Dcard 等不支援站點，`{status, url, error}` |
| `failed` | `{status, url, error}` |

---

## Colab 呼叫範例

```python
import requests, time
CRAWLER = "https://content-crawler-xxx.run.app"
API_KEY = "iok_..."   # content-analyser 後台核發、具 crawl 權限的金鑰

# 提交非同步爬取
job = requests.post(f"{CRAWLER}/api/crawl/batch",
    json={"urls": ["https://example.com/a", "https://example.com/b"]},
    headers={"X-API-Key": API_KEY}).json()

# 輪詢直到完成
while True:
    s = requests.get(f"{CRAWLER}/api/crawl/{job['job_id']}",
        headers={"X-API-Key": API_KEY}).json()
    if s["status"] in ("completed", "failed"):
        break
    time.sleep(3)

for r in s["results"]:
    print(r["status"], r.get("title"), r.get("length"))
```

---

## 環境變數

| 變數 | 來源 | 說明 |
|------|------|------|
| `CRAWLER_API_KEY` | Secret Manager | 系統存取金鑰 |
| `GENAI_API_KEY` | Secret Manager | Gemini（selector 輔助）|
| `GOOGLE_CLOUD_PROJECT` | Cloud Run | Firestore（crawl_jobs / api_keys 驗證）|
| `CHROME_BIN` / `CHROMEDRIVER_PATH` | Dockerfile | Chrome 固定路徑 |

部署見專案根目錄 `deploy.sh`。
