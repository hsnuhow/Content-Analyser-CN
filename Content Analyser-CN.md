# 產品開發規格書：內容 AI 分析與優化平台 (Pure Flask 版)

## 1. 專案總覽 (Project Overview)
本專案旨在將原有的 Colab 爬蟲與 AI 分析工具，遷移至 Google Cloud Platform (GCP) 與 Firebase 的 Serverless 架構。目標是建立一個穩定、單一專案結構（Monorepo）的網頁應用程式。

* **核心變更**：移除 `ipywidgets` 與 Colab 特定依賴，改用 Python Flask + Jinja2 模板渲染網頁。
* **部署目標**：Google Cloud Run (完全託管的 Docker 容器服務)。
* **技術特點**：純 Python 專案，結合 Selenium 爬蟲、Firebase Auth 身分驗證與 Firestore 即時資料庫。

---

## 2. 雲端基礎建設配置 (Cloud Infrastructure)

在開發前，必須在 GCP 與 Firebase Console 完成以下準備。

### 2.1 Google Cloud Platform (GCP)
需啟用以下 API 與服務：
1.  **Cloud Run API**：核心服務，用於託管 Flask 應用程式與爬蟲背景任務。
2.  **Artifact Registry API**：用於儲存構建好的 Docker 容器映像檔。
3.  **Secret Manager API**：用於安全儲存系統金鑰 (Gemini API Key, Google Drive Credentials)。
4.  **Vertex AI API**：(選用) 若需要使用 GCP 的 Gemini 模型。
5.  **Google Docs & Drive API**：
    * Cloud Run 服務帳戶需有相關權限。
6.  **Cloud Build API**：用於自動化構建 Docker Image。

### 2.2 Firebase 專案設定
1.  **Authentication**：
    * 啟用 **Google Sign-In** 提供者。
    * 在「授權網域 (Authorized domains)」中加入 Cloud Run 部署後的網址。
2.  **Firestore Database**：
    * 建立資料庫。
    * 用於儲存使用者設定 (User Config) 與任務狀態 (Tasks)。

---

## 3. 系統架構 (System Architecture)

### 3.0 服務拆分：獨立爬蟲 (Independent Crawler Service) [Update 2026.06]
爬蟲已自主程式拆分為一個**完全獨立、透過 API 操作的 Cloud Run 服務**，部署為兩個服務：

1.  **`content-analyser`（Web 應用）**：Flask + OAuth + Firestore，負責登入、任務管理、報表匯出。不再內嵌爬蟲，也不再安裝 Chrome。
2.  **`content-crawler`（獨立爬蟲服務）**：位於 `crawler-service/`，提供受金鑰保護的 HTTP API，內部以無頭瀏覽器（undetected-chromedriver）爬取內容。爬取核心嚴格對齊已驗證的 Colab v3.8。

**互動方式**：
*   主程式 `app/worker.py` 透過 `app/crawler_client.py` 以 HTTP `POST /api/scrape` 呼叫爬蟲服務。
*   每個請求都必須帶上 `X-API-Key`（與 Secret Manager 中的 `CRAWLER_API_KEY` 一致），不符回 401。
*   主程式以環境變數 `CRAWLER_SERVICE_URL` 取得爬蟲服務位址。

**爬蟲 API**：
*   `GET  /health`：健康檢查（不需金鑰）。
*   `POST /api/scrape`：同步爬取單一網址，body `{url, use_gemini, gemini_api_key?}`，回傳 `{status,title,content,length,error}`。

**新增 Secret**：`CRAWLER_API_KEY`（爬蟲 API 存取金鑰）。


### 3.1 身分驗證與授權 (Authentication & Authorization) [Update 2025.12]
全站採用 Google OAuth 2.0 強制登入機制，區分兩種角色：

1.  **系統管理員 (Super Admin)**
    *   **唯一識別**：僅限 `how.penguin@gmail.com`。
    *   **權限**：
        *   存取 `/admin` 系統管理儀表板。
        *   檢視與更新系統預設金鑰 (Stored in Secret Manager)。
        *   執行爬蟲任務。
2.  **一般使用者 (User)**
    *   **識別**：任何其他 Google 帳號。
    *   **權限**：
        *   存取 `/dashboard` 執行爬蟲任務。
        *   存取 `/profile` 設定個人的 Gemini API Key。
        *   **不可** 存取系統管理介面或修改系統金鑰。

### 3.2 使用者設定管理 (User Configuration)
使用 Firestore 儲存每位使用者的個人化設定。

*   **Collection**: `users`
*   **Document ID**: `user_email` (使用 Email 作為 Key 以方便管理)
*   **Fields**:
    *   `role`: String ("admin" or "user")
    *   `gemini_api_key`: String (使用者自訂的 API Key，加密儲存或明碼視需求而定，建議應用層加密)
    *   `created_at`: Timestamp
    *   `last_login`: Timestamp

**API Key 使用邏輯**：
1.  系統執行爬蟲或分析時，優先檢查該使用者的 `gemini_api_key`。
2.  若使用者未設定，則檢查是否為 Admin。若是 Admin，可 fallback 使用系統預設 Key。
3.  若一般使用者未設定 Key，則提示需設定後才能使用進階 AI 功能。

### 3.3 系統金鑰管理 (System Secrets)
使用 **Google Cloud Secret Manager** 儲存敏感資訊，不寫入 Firestore。

*   **Secret: `SYSTEM_GEMINI_KEY`**: 系統預設的 Gemini API Key (供管理員測試或 fallback 使用)。
*   **Secret: `APP_ACCESS_KEY`**: (已棄用) 舊有的 admin123 密碼驗證將被 OAuth 取代。

**管理介面 (`/admin`)**：
*   提供 GUI 讓管理員更新 Secret Manager 中的值。
*   後端透過 `google-cloud-secret-manager` SDK 執行更新操作。

---

## 4. 功能需求與邏輯 (Functional Requirements)

### 4.1 登入與路由保護
*   **`/login`**: 登入頁面，僅顯示 "Sign in with Google"。
*   **`/logout`**: 清除 Session 並登出。
*   **Middleware**: 所有 `/submit_task`, `/profile`, `/admin` 等路由皆需經過 `@login_required` 檢查。
*   **Admin Middleware**: `/admin` 路由額外檢查 `session['user_email'] == 'how.penguin@gmail.com'`。

### 4.2 核心爬蟲與分析邏輯 (Backend Worker)
邏輯保持不變，但在初始化 Crawler 時，需傳入正確的 API Key：
```python
# 虛擬碼範例
user_config = firestore.get(user_email)
api_key = user_config.get('gemini_api_key')

if not api_key and is_admin(user_email):
    api_key = get_system_secret('SYSTEM_GEMINI_KEY')

crawler = HeadlessCrawler(api_key=api_key)
```

---

## 5. Dockerfile 規格
(保持不變，需確保安裝 Chrome 與相關依賴)
