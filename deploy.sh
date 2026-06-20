#!/bin/bash
set -e

# =====================================================================
# deploy.sh — Cloud Run 服務完整部署腳本
#
# 部署順序：
#   1. content-crawler    (爬蟲微服務，4Gi Chrome 環境)
#   2. analysis-pipeline  (分析引擎，2Gi NLP + Vertex AI)
#   3. content-analyser   (Web UI + 控制平面，1Gi 輕量)
#
# ⚠️ 第四個服務 search-extent（§7 真實搜尋接地）本腳本【不】部署，
#    需單獨手動部署（目前阻塞於 Google Ads ADS_DEVELOPER_TOKEN）。
#    部署 search-extent 後，content-analyser / analysis-pipeline 需另行
#    注入 SEARCH_EXTENT_SERVICE_URL 與 SEARCH_EXTENT_API_KEY（見 CLAUDE.md 附錄 D）。
#
# content-crawler 的執行期環境變數（標準化：機密一律走 Secret Manager）：
#   - 機密 → --set-secrets：CRAWLER_API_KEY / GENAI_API_KEY +
#     Tier 3 代理憑證 PROXY_HOST / PROXY_PORT / PROXY_USER / PROXY_PASS / PROXY_PROVIDER
#   - 非機密設定 → --set-env-vars：ENABLE_YOUTUBE_TRANSCRIPT
#   - Tier 3 on/off 不在此：由後台 toggle（Firestore system/config.tier3_enabled）控制。
#   PROXY_* 五個 secret 可由管理後台「Secret Manager 金鑰管理」直接建立/更新（不存在會自動建立），
#   或用下方 gcloud 指令一次建立。本腳本完整定義 crawler 環境、不依賴 console 手動設定。
#   本地除錯：proxy 憑證放 .env（已 gitignore），僅 debug 模式取用，不進正式環境。
#
# 前置需求：Secret Manager 中必須已建立以下 secrets：
#   CRAWLER_API_KEY   - 爬蟲服務存取金鑰 (openssl rand -hex 32)
#   ANALYSIS_API_KEY  - 分析服務存取金鑰 (openssl rand -hex 32)
#   GENAI_API_KEY     - Gemini API Key（爬蟲 selector 輔助用）
#   GOOGLE_CLIENT_ID  - Google OAuth Client ID
#   GOOGLE_CLIENT_SECRET - Google OAuth Client Secret
#   FLASK_SECRET_KEY  - Flask Session 加密金鑰
#   PROXY_HOST / PROXY_PORT / PROXY_USER / PROXY_PASS / PROXY_PROVIDER
#                     - content-crawler Tier 3 住宅代理憑證（Decodo），由後台或維運者建立
#
# 首次部署後，請執行：
#   bash setup_admin.sh  (設定管理員 email)
# =====================================================================

PROJECT_ID=$(gcloud config get-value project)
if [ -z "$PROJECT_ID" ]; then
  echo "Error: 無法取得 GCP Project ID。請先執行 'gcloud config set project YOUR_PROJECT_ID'。"
  exit 1
fi

SERVICE_NAME="content-analyser"
CRAWLER_SERVICE="content-crawler"
ANALYSIS_SERVICE="analysis-pipeline"
REGION="asia-east1"
# Cloud Tasks 佇列（並行安全的爬蟲/擷取/研究派送）。佇列需先手動建立 + 授權（見檔頭）。
# 建好佇列後，把 content-crawler 的 CRAWLER_USE_QUEUE 設為 1 才會啟用（預設 0 走背景執行緒 fallback）。
TASKS_QUEUE="${TASKS_QUEUE:-crawler-tasks}"
TASKS_LOCATION="${TASKS_LOCATION:-$REGION}"

echo "========================================================"
echo "Content Analyser CN — 完整部署（三個 Cloud Run 服務）"
echo "Project          : $PROJECT_ID"
echo "Region           : $REGION"
echo "Web Service      : $SERVICE_NAME"
echo "Crawler Service  : $CRAWLER_SERVICE"
echo "Analysis Service : $ANALYSIS_SERVICE"
echo "========================================================"
echo ""
echo "[前置需求] 請確認 Secret Manager 已建立以下 secrets："
echo "  CRAWLER_API_KEY / ANALYSIS_API_KEY / GENAI_API_KEY"
echo "  GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / FLASK_SECRET_KEY"
echo "  PROXY_HOST / PROXY_PORT / PROXY_USER / PROXY_PASS / PROXY_PROVIDER（Tier 3 代理；可由後台建立）"
echo ""

read -p "是否繼續部署？(y/N) " confirm
if [[ $confirm != [yY] && $confirm != [yY][eE][sS] ]]; then
  echo "部署已取消。"
  exit 0
fi

# ========================================================
# 1) 部署 content-crawler（爬蟲微服務）
# ========================================================
echo ""
echo ">>> [1/3] 建立並部署 content-crawler..."
gcloud builds submit crawler-service --tag gcr.io/$PROJECT_ID/$CRAWLER_SERVICE

# crawler 環境完全由本腳本定義（標準、自洽）：
#   非機密 → --set-env-vars；機密（API 金鑰 + 住宅代理憑證）→ --set-secrets。
#   前置：PROXY_* 六個 secret 需已建立（見檔頭與 §6.2），否則此步會失敗。
# ⚠️ concurrency=1：佇列模式下每台 instance 一次只跑 1 個 Chrome（杜絕多任務疊加 OOM）。
#   CRAWLER_USE_QUEUE=1 啟用 Cloud Tasks 佇列（前置：佇列 crawler-tasks 已建、SA 有 cloudtasks.enqueuer，
#   2026-06-18 確認皆就緒）。設 0 或不設則回退背景執行緒 fallback（多用戶並行有 OOM 風險）。
gcloud run deploy $CRAWLER_SERVICE \
  --image gcr.io/$PROJECT_ID/$CRAWLER_SERVICE \
  --platform managed \
  --region $REGION \
  --allow-unauthenticated \
  --memory 4Gi \
  --cpu 2 \
  --timeout 300 \
  --concurrency 1 \
  --max-instances 10 \
  --set-env-vars "ENABLE_YOUTUBE_TRANSCRIPT=1,GOOGLE_CLOUD_PROJECT=$PROJECT_ID,TASKS_QUEUE=$TASKS_QUEUE,TASKS_LOCATION=$TASKS_LOCATION,CRAWLER_USE_QUEUE=1" \
  --set-secrets "CRAWLER_API_KEY=CRAWLER_API_KEY:latest,GENAI_API_KEY=GENAI_API_KEY:latest,PROXY_HOST=PROXY_HOST:latest,PROXY_PORT=PROXY_PORT:latest,PROXY_USER=PROXY_USER:latest,PROXY_PASS=PROXY_PASS:latest,PROXY_PROVIDER=PROXY_PROVIDER:latest"

CRAWLER_URL=$(gcloud run services describe $CRAWLER_SERVICE \
  --region $REGION --platform managed --format 'value(status.url)')
echo ">>> content-crawler URL: $CRAWLER_URL"

# WORKER_URL = crawler 自身 URL（Cloud Tasks 把任務 POST 回本服務的 /api/*/run）。
# 首次部署時 URL 才已知，故以二次 update 注入（URL 穩定，之後部署沿用）。
gcloud run services update $CRAWLER_SERVICE --region $REGION --platform managed \
  --update-env-vars "WORKER_URL=$CRAWLER_URL" >/dev/null
echo ">>> content-crawler WORKER_URL 已注入：$CRAWLER_URL"

# ========================================================
# 2) 部署 analysis-pipeline（分析引擎）
# ========================================================
echo ""
echo ">>> [2/3] 建立並部署 analysis-pipeline..."
gcloud builds submit analysis-service --tag gcr.io/$PROJECT_ID/$ANALYSIS_SERVICE

gcloud run deploy $ANALYSIS_SERVICE \
  --image gcr.io/$PROJECT_ID/$ANALYSIS_SERVICE \
  --platform managed \
  --region $REGION \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 1 \
  --timeout 600 \
  --concurrency 2 \
  --max-instances 10 \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=$PROJECT_ID" \
  --set-secrets "ANALYSIS_API_KEY=ANALYSIS_API_KEY:latest"

ANALYSIS_URL=$(gcloud run services describe $ANALYSIS_SERVICE \
  --region $REGION --platform managed --format 'value(status.url)')
echo ">>> analysis-pipeline URL: $ANALYSIS_URL"

# search-extent（搜尋情報層）獨立部署；此處只取 URL 注入 content-analyser（內容發現用）。
# 若 search-extent 尚未部署則為空，content-analyser 端會回「服務未接上」而非崩潰。
SEARCH_EXTENT_URL=$(gcloud run services describe search-extent \
  --region $REGION --platform managed --format 'value(status.url)' 2>/dev/null || echo "")
echo ">>> search-extent URL: ${SEARCH_EXTENT_URL:-（未部署，內容發現停用）}"

# ========================================================
# 3) 部署 content-analyser（Web UI + 控制平面）
# ========================================================
echo ""
echo ">>> [3/3] 建立並部署 content-analyser..."
gcloud builds submit --tag gcr.io/$PROJECT_ID/$SERVICE_NAME

gcloud run deploy $SERVICE_NAME \
  --image gcr.io/$PROJECT_ID/$SERVICE_NAME \
  --platform managed \
  --region $REGION \
  --allow-unauthenticated \
  --memory 1Gi \
  --cpu 1 \
  --timeout 300 \
  --max-instances 10 \
  --set-env-vars "CRAWLER_SERVICE_URL=$CRAWLER_URL,ANALYSIS_SERVICE_URL=$ANALYSIS_URL,SEARCH_EXTENT_SERVICE_URL=$SEARCH_EXTENT_URL,GOOGLE_CLOUD_PROJECT=$PROJECT_ID,ENFORCE_ORIGIN_TOKEN=1" \
  --set-secrets "GOOGLE_CLIENT_ID=GOOGLE_CLIENT_ID:latest,GOOGLE_CLIENT_SECRET=GOOGLE_CLIENT_SECRET:latest,SECRET_KEY=FLASK_SECRET_KEY:latest,GENAI_API_KEY=GENAI_API_KEY:latest,CRAWLER_API_KEY=CRAWLER_API_KEY:latest,ANALYSIS_API_KEY=ANALYSIS_API_KEY:latest,SEARCH_EXTENT_API_KEY=SEARCH_EXTENT_API_KEY:latest,ORIGIN_VERIFY_TOKEN=ORIGIN_VERIFY_TOKEN:latest"

WEB_URL=$(gcloud run services describe $SERVICE_NAME \
  --region $REGION --platform managed --format 'value(status.url)')

echo ""
echo "========================================================"
echo "部署完成！"
echo "Web App          : $WEB_URL"
echo "Crawler          : $CRAWLER_URL"
echo "Analysis Pipeline: $ANALYSIS_URL"
echo ""
echo "後續步驟："
echo "  1. 將 $WEB_URL/callback 加入 Google OAuth 的授權重新導向 URI"
echo "  2. 執行 bash setup_admin.sh 設定管理員帳號"
echo "========================================================"
