# CLAUDE.md — Content Analyser CN 開發規範

本文件供 Claude Code 在此專案中工作時參考。包含架構說明、開發規範、指令與環境變數清單。

---

## 專案概覽

**給康泰用的內容爬蟲與 AI 分析平台。**

將文章 URL 貼入後，系統自動爬取全文並輸出 Word 報告（.docx）。
採用兩個獨立的 Google Cloud Run 服務，以 HTTP API 互相溝通。

- **GitHub**: https://github.com/hsnuhow/Content-Analyser-CN
- **管理員**: how.penguin@gmail.com
- **語言要求**: 程式碼外的所有說明、changelog、commit message 請使用**正體中文**

---

## 系統架構

```
使用者 (瀏覽器)
    │  Google OAuth
    ▼
┌─────────────────────────────────────────┐
│  content-analyser (Cloud Run)           │
│  Flask Web UI + Firestore 任務管理      │
│  app/__init__.py    — App Factory       │
│  app/routes.py      — 主路由           │
│  app/admin_routes.py — /admin 管理介面 │
│  app/worker.py      — 背景 Pipeline    │
│  app/crawler_client.py — 爬蟲 HTTP 客戶端 │
│  app/export_utils.py — .docx 匯出      │
│  app/services.py    — Firebase 初始化  │
└─────────────────────────────────────────┘
    │  POST /api/scrape
    │  X-API-Key: CRAWLER_API_KEY
    ▼
┌─────────────────────────────────────────┐
│  content-crawler (Cloud Run)            │
│  獨立爬蟲微服務，含 Chrome + Selenium  │
│  crawler-service/app.py   — API 入口   │
│  crawler-service/crawler.py — 核心爬蟲 │
└─────────────────────────────────────────┘
    │
    ▼  (低置信度時)
Gemini API (google-genai)
```

---

## 兩個服務的職責

### content-analyser（主程式）
- Flask Web UI，Jinja2 模板渲染
- Google OAuth 2.0 登入，兩種角色（Admin / User）
- Firestore 持久化任務狀態與使用者設定
- 透過 `threading` 在背景執行分析 Pipeline
- 呼叫 content-crawler 服務（HTTP），不執行 Chrome
- 匯出 .docx 報告

### content-crawler（爬蟲微服務）
- 完全獨立的 Flask API 服務
- 所有端點需 `X-API-Key` Header 驗證（`hmac.compare_digest`）
- 使用 `undetected-chromedriver` + `selenium`，對齊 Colab v3.8
- 可被任何外部系統呼叫（Colab、Claude Cowork 等）

---

## 爬蟲服務 API

**Base URL**: `CRAWLER_SERVICE_URL`（部署後取得）

### GET /health
不需 API Key，供 Cloud Run 探活與外部監控。

```json
{
  "status": "ok",
  "service": "content-crawler",
  "version": "1.1.0",
  "chrome": "Google Chrome 122.0.6261.94",
  "api_key_configured": true
}
```

### POST /api/scrape
爬取單一 URL。

**Request Headers**: `X-API-Key: <CRAWLER_API_KEY>`

```json
{
  "url": "https://example.com/article",
  "use_gemini": false,
  "gemini_api_key": "AIza..."
}
```

**Response（成功）**:
```json
{"status": "success", "url": "...", "title": "...", "content": "...", "length": 1234}
```

**Response（略過/失敗）**:
```json
{"status": "skipped", "url": "...", "error": "URL is an article list page."}
{"status": "failed",  "url": "...", "error": "..."}
```

### POST /api/scrape/batch
批次爬取，最多 20 個 URL，依序同步執行。

```json
{
  "urls": ["https://...", "https://..."],
  "use_gemini": false,
  "gemini_api_key": "AIza..."
}
```

**Response**:
```json
{
  "results": [<result>, ...],
  "total": 2,
  "succeeded": 1,
  "failed": 1
}
```

---

## 從 Colab 或外部系統呼叫

```python
import requests

CRAWLER_URL = "https://content-crawler-xxxxx.run.app"
API_KEY = "your-crawler-api-key"

# 單一 URL
result = requests.post(
    f"{CRAWLER_URL}/api/scrape",
    json={"url": "https://example.com/article", "use_gemini": True, "gemini_api_key": "AIza..."},
    headers={"X-API-Key": API_KEY},
    timeout=300
).json()

# 批次
batch = requests.post(
    f"{CRAWLER_URL}/api/scrape/batch",
    json={"urls": ["https://...", "https://..."], "use_gemini": False},
    headers={"X-API-Key": API_KEY},
    timeout=1500
).json()
```

---

## Firestore 資料結構

```
users/{email}
  ├── gemini_api_key: str
  ├── updated_at: timestamp
  └── projects/{project_id}
        ├── status: "pending" | "completed" | "failed" | "cancelled"
        ├── progress: int (0–100)
        ├── log: str
        ├── report_title: str
        ├── input_urls: [str]
        ├── use_gemini: bool
        ├── created_at: timestamp
        └── pages/{auto_id}
              ├── url: str
              ├── status: "success" | "failed" | "skipped"
              ├── title: str
              ├── content: str
              ├── length: int
              ├── error: str
              └── crawled_at: timestamp
```

---

## 環境變數

### content-analyser（主程式）
| 變數 | 來源 | 說明 |
|------|------|------|
| `SECRET_KEY` | Secret Manager: `FLASK_SECRET_KEY` | Flask Session 加密金鑰 |
| `GOOGLE_CLIENT_ID` | Secret Manager | Google OAuth Client ID |
| `GOOGLE_CLIENT_SECRET` | Secret Manager | Google OAuth Client Secret |
| `CRAWLER_SERVICE_URL` | deploy.sh 自動注入 | 爬蟲服務的 Cloud Run URL |
| `CRAWLER_API_KEY` | Secret Manager | 呼叫爬蟲服務用的 API Key |
| `GENAI_API_KEY` | Secret Manager | 系統預設 Gemini Key（Admin fallback）|
| `FLASK_DEBUG` | 本地 .env | `1` = Dev mode，啟用自動登入 |

### content-crawler（爬蟲服務）
| 變數 | 來源 | 說明 |
|------|------|------|
| `CRAWLER_API_KEY` | Secret Manager | API 驗證金鑰（與主程式相同） |
| `GENAI_API_KEY` | Secret Manager | 服務預設 Gemini Key |
| `CHROME_BIN` | Dockerfile ENV | Chrome 執行檔路徑（固定 `/usr/bin/google-chrome`）|
| `CHROMEDRIVER_PATH` | Dockerfile ENV | ChromeDriver 路徑（固定 `/usr/bin/chromedriver`）|

### Secret Manager 金鑰清單
建立前請先準備：
```bash
# 產生 CRAWLER_API_KEY（隨機強金鑰）
openssl rand -hex 32

# 建立 secrets（首次）
echo -n "your-value" | gcloud secrets create SECRET_NAME --data-file=-
# 更新 secrets（後續）
echo -n "your-value" | gcloud secrets versions add SECRET_NAME --data-file=-
```

---

## 常用指令

### 本地開發
```bash
# 啟動虛擬環境
source .venv/bin/activate

# 安裝主程式相依
pip install -r requirements.txt

# 安裝爬蟲服務相依（如需本地測試）
pip install -r crawler-service/requirements.txt

# 啟動主程式（Dev 模式，自動登入 how.penguin@gmail.com）
FLASK_DEBUG=1 python main.py

# 或用 devserver.sh
bash devserver.sh
```

### 語法檢查
```bash
# 主程式
python3 -m py_compile app/routes.py app/worker.py app/crawler_client.py app/services.py app/admin_routes.py app/export_utils.py main.py

# 爬蟲服務
python3 -m py_compile crawler-service/app.py crawler-service/crawler.py

# Shell scripts
bash -n deploy.sh
```

### 部署
```bash
# 設定 GCP 專案
gcloud config set project YOUR_PROJECT_ID

# 部署兩個服務（互動確認後執行）
bash deploy.sh
```

---

## 角色與權限

| 角色 | 識別 | 可存取 |
|------|------|--------|
| Admin | `how.penguin@gmail.com` | 全部，包含 `/admin` |
| User | 其他 Google 帳號 | `/`、`/profile`、`/submit_task` |

**API Key 使用順序**（爬蟲任務）：
1. 使用者的個人 Gemini Key（Firestore `gemini_api_key`）
2. 系統預設 Key（`GENAI_API_KEY` 環境變數）
3. 若均無，不使用 Gemini（純啟發式爬取）

---

## 開發規範

### 不要做的事
- 不在主程式 Dockerfile 安裝 Chrome（爬蟲已獨立）
- 不直接 import `crawler.py`（只透過 `crawler_client` HTTP 呼叫）
- 不將金鑰寫入程式碼或提交 `.env`
- 不跳過 `X-API-Key` 驗證

### Changelog
每次修改後更新 `changelog.md`，格式：
```
## yyyy-mm-dd (Description)
- **Category (Module)**: 目的、解決方式、修改的函式
```

### Commit 訊息
使用正體中文，格式：
```
<類型>：<一行摘要>

- 細節說明...

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```

類型：`新增`、`修正`、`重構`、`補強`、`文件`
