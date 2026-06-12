# 部署前準備清單 — Content Analyser CN

**版本：** 1.0  
**適用：** 首次部署三服務至 Google Cloud Run  
**預估時間：** 約 60–90 分鐘（含 GCP 設定）

---

## 總覽：你需要準備的東西

| # | 項目 | 從哪裡取得 | 必要性 |
|---|------|-----------|--------|
| 1 | GCP Project | Google Cloud Console | 必要 |
| 2 | 啟用 7 個 GCP API | Cloud Console | 必要 |
| 3 | Firebase 專案 + Firestore | Firebase Console | 必要 |
| 4 | Google OAuth 憑證 | Cloud Console | 必要 |
| 5 | 6 個 Secret Manager 金鑰 | 自行產生 | 必要 |
| 6 | 用戶的 Gemini / Claude Key | 用戶自備 | 用戶自理 |
| 7 | IAM 權限（Vertex AI）| Cloud Console | 必要 |

---

## Step 1：建立 / 確認 GCP Project

```bash
# 查看現有 project
gcloud projects list

# 設定要使用的 project（記下 PROJECT_ID）
gcloud config set project YOUR_PROJECT_ID

# 確認
gcloud config get-value project
```

**請提供給我：** 你的 `PROJECT_ID`

---

## Step 2：啟用必要的 GCP API

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  firestore.googleapis.com \
  aiplatform.googleapis.com \
  iamcredentials.googleapis.com
```

| API | 用途 |
|-----|------|
| `run` | Cloud Run（三服務運行）|
| `cloudbuild` | 建置 Docker image |
| `artifactregistry` | 儲存 image |
| `secretmanager` | 金鑰管理 |
| `firestore` | 資料庫 |
| `aiplatform` | **Vertex AI Embedding（語意分群）** |
| `iamcredentials` | Service Account 認證 |

> ⚠️ `aiplatform`（Vertex AI）是 analysis-pipeline 語意分群必須的，別漏掉。

---

## Step 3：建立 Firestore 資料庫

```bash
# 建立 Firestore（Native 模式，選擇與 Cloud Run 相同 region）
gcloud firestore databases create --location=asia-east1
```

或在 Firebase Console：
1. 前往 https://console.firebase.google.com
2. 選擇你的 GCP Project（Firebase 與 GCP 共用 project）
3. Build → Firestore Database → 建立資料庫 → **Native 模式** → 選 `asia-east1`

> Firestore 不需要預先建立 collection，程式會自動建立。

---

## Step 4：設定 Google OAuth

### 4.1 建立 OAuth 同意畫面

1. Cloud Console → API 和服務 → OAuth 同意畫面
2. User Type：**External**（或 Internal，若是 Google Workspace）
3. 填入應用程式名稱、支援信箱
4. Scopes：加入 `openid`、`email`、`profile`
5. Test users：先加入你自己的 email（External + 測試階段需要）

### 4.2 建立 OAuth Client ID

1. Cloud Console → API 和服務 → 憑證 → 建立憑證 → OAuth 用戶端 ID
2. 應用程式類型：**網頁應用程式**
3. **已授權的重新導向 URI**：
   - 部署後才知道 Web URL，先填一個暫時的，部署後再回來補正確的：
   - `https://content-analyser-xxxxx.run.app/callback`
4. 建立後記下 **Client ID** 和 **Client Secret**

**請提供給我（或自行保管）：**
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`

> 📌 部署完成後，務必回到這裡把實際的 Cloud Run URL + `/callback` 加進「已授權的重新導向 URI」，否則登入會失敗。

---

## Step 5：建立 Secret Manager 金鑰

### 5.1 產生兩個服務存取金鑰

```bash
# 爬蟲服務金鑰
openssl rand -hex 32
# → 記下這個值，等下用

# 分析服務金鑰
openssl rand -hex 32
# → 記下這個值，等下用
```

### 5.2 建立全部 6 個 secrets

```bash
# 1. Flask Session 加密金鑰
echo -n "$(openssl rand -hex 32)" | \
  gcloud secrets create FLASK_SECRET_KEY --data-file=-

# 2. 爬蟲服務存取金鑰（用 5.1 產生的第一個值）
echo -n "你的爬蟲金鑰" | \
  gcloud secrets create CRAWLER_API_KEY --data-file=-

# 3. 分析服務存取金鑰（用 5.1 產生的第二個值）
echo -n "你的分析金鑰" | \
  gcloud secrets create ANALYSIS_API_KEY --data-file=-

# 4. Google OAuth Client ID（Step 4 取得）
echo -n "你的-client-id.apps.googleusercontent.com" | \
  gcloud secrets create GOOGLE_CLIENT_ID --data-file=-

# 5. Google OAuth Client Secret（Step 4 取得）
echo -n "你的-client-secret" | \
  gcloud secrets create GOOGLE_CLIENT_SECRET --data-file=-

# 6. 爬蟲 selector 輔助用的 Gemini Key（你自己的 Gemini API Key）
echo -n "你的-gemini-key" | \
  gcloud secrets create GENAI_API_KEY --data-file=-
```

### 5.3 確認全部建立完成

```bash
gcloud secrets list --format="table(name)"
```

預期看到 6 個：
```
FLASK_SECRET_KEY
CRAWLER_API_KEY
ANALYSIS_API_KEY
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
GENAI_API_KEY
```

---

## Step 6：設定 Service Account 權限

Cloud Run 預設使用 Compute Engine 預設 Service Account。它需要存取 Secret Manager、Firestore、Vertex AI。

```bash
PROJECT_ID=$(gcloud config get-value project)
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

# 存取 Secret Manager
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${SA}" \
  --role="roles/secretmanager.secretAccessor"

# 存取 Firestore
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${SA}" \
  --role="roles/datastore.user"

# 存取 Vertex AI（語意 Embedding）
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${SA}" \
  --role="roles/aiplatform.user"
```

> ⚠️ `aiplatform.user` 是 analysis-pipeline 呼叫 Vertex AI Embedding 必須的權限。

---

## Step 7：執行部署

```bash
# 確認在專案根目錄
cd /path/to/Content-Analyser-CN

# 確認在 main 分支、工作區乾淨
git branch --show-current   # → main
git status                  # → working tree clean

# 執行部署（會依序部署三個服務，互動確認）
bash deploy.sh
```

部署腳本會：
1. 部署 `content-crawler`（4Gi，含 Chrome）
2. 部署 `analysis-pipeline`（2Gi，NLP + Vertex AI）
3. 部署 `content-analyser`（1Gi，自動注入前兩者的 URL）

完成後會印出三個服務的 URL，**記下 Web App URL**。

---

## Step 8：部署後設定

### 8.1 補正 OAuth 重新導向 URI

回到 Step 4.2 的 OAuth Client 設定，把實際的 Web URL 加入：
```
https://content-analyser-xxxxx.run.app/callback
```

### 8.2 設定系統管理員

```bash
# 複製範本
cp setup_admin.sh.example setup_admin.sh

# 編輯 setup_admin.sh，填入：
#   PROJECT_ID="你的-project-id"
#   ADMIN_EMAIL="how.penguin@gmail.com"

# 執行（寫入 Firestore system/config.admin_email）
bash setup_admin.sh
```

---

## Step 9：驗證部署

```bash
REGION="asia-east1"

# 取得 URL
CRAWLER_URL=$(gcloud run services describe content-crawler --region $REGION --format 'value(status.url)')
ANALYSIS_URL=$(gcloud run services describe analysis-pipeline --region $REGION --format 'value(status.url)')
WEB_URL=$(gcloud run services describe content-analyser --region $REGION --format 'value(status.url)')

# 健康檢查（兩個引擎）
curl "$CRAWLER_URL/health"
# 預期：{"status":"ok","service":"content-crawler","api_key_configured":true,...}

curl "$ANALYSIS_URL/health"
# 預期：{"status":"ok","service":"analysis-pipeline","api_key_configured":true,...}

echo "Web App: $WEB_URL"
```

### 端到端測試（手動）

```
□ 開啟 Web URL，用 Google 登入
□ 管理員帳號 → 直接進入「我的專案」
□ 非管理員帳號 → 看到「等待授權」頁
□ 管理員 → /admin/users → 批准該用戶
□ 建立 Project → 設定 LLM Key（你的 Gemini/Claude Key）
□ 貼入測試 contents JSON → 提交分析
□ 進度條更新 → 完成 → 看到 Markdown 報告
□ 下載 .md
```

---

## 需要你提供 / 確認的資訊

請提供以下資訊，我可以幫你檢查設定或產生對應指令：

| 項目 | 你的值 |
|------|--------|
| GCP Project ID | ？ |
| 部署 Region（預設 asia-east1）| ？ |
| 管理員 Email | how.penguin@gmail.com |
| OAuth Client ID | ？（敏感，可自行保管）|
| 是否已啟用 Vertex AI | ？ |
| 你的 Gemini API Key（爬蟲用）| ？（敏感，可自行保管）|

---

## 費用預估（月）

| 項目 | 預估 |
|------|------|
| Cloud Run（低流量，scale-to-zero）| < $1 |
| Vertex AI Embedding（50 次分析）| < $0.50 |
| Firestore（小規模讀寫）| 免費額度內 |
| Secret Manager | < $0.10 |
| **合計** | **約 $1–2（台幣 30–60 元）** |

> LLM 分析費用由用戶自備 Key 負擔，不計入系統成本。

---

## 常見問題排查

| 症狀 | 可能原因 | 解法 |
|------|---------|------|
| 登入後 redirect_uri_mismatch | OAuth 重新導向 URI 未設定 | Step 8.1 補正 |
| /admin 顯示「尚未設定管理員」| 未執行 setup_admin.sh | Step 8.2 |
| 分析卡在 pending | analysis-pipeline 無法連線 | 檢查 ANALYSIS_SERVICE_URL 注入 |
| 語意分群失敗（報告仍生成）| Vertex AI 權限不足 | Step 6 的 aiplatform.user |
| health 顯示 api_key_configured: false | Secret 未注入 | 檢查 deploy.sh 的 --set-secrets |
| 爬蟲 401 | CRAWLER_API_KEY 不一致 | 確認兩服務用同一把 secret |
