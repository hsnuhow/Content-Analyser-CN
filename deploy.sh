#!/bin/bash
set -e

# =====================================================================
# deploy.sh — 三個 Cloud Run 服務完整部署腳本
#
# 部署順序：
#   1. content-crawler    (爬蟲微服務，4Gi Chrome 環境)
#   2. analysis-pipeline  (分析引擎，2Gi NLP + Vertex AI)
#   3. content-analyser   (Web UI + 控制平面，1Gi 輕量)
#
# 前置需求：Secret Manager 中必須已建立以下 secrets：
#   CRAWLER_API_KEY   - 爬蟲服務存取金鑰 (openssl rand -hex 32)
#   ANALYSIS_API_KEY  - 分析服務存取金鑰 (openssl rand -hex 32)
#   GENAI_API_KEY     - Gemini API Key（爬蟲 selector 輔助用）
#   GOOGLE_CLIENT_ID  - Google OAuth Client ID
#   GOOGLE_CLIENT_SECRET - Google OAuth Client Secret
#   FLASK_SECRET_KEY  - Flask Session 加密金鑰
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

gcloud run deploy $CRAWLER_SERVICE \
  --image gcr.io/$PROJECT_ID/$CRAWLER_SERVICE \
  --platform managed \
  --region $REGION \
  --allow-unauthenticated \
  --memory 4Gi \
  --cpu 2 \
  --timeout 300 \
  --concurrency 4 \
  --clear-env-vars \
  --set-secrets "CRAWLER_API_KEY=CRAWLER_API_KEY:latest,GENAI_API_KEY=GENAI_API_KEY:latest"

CRAWLER_URL=$(gcloud run services describe $CRAWLER_SERVICE \
  --region $REGION --platform managed --format 'value(status.url)')
echo ">>> content-crawler URL: $CRAWLER_URL"

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
  --set-env-vars "GOOGLE_CLOUD_PROJECT=$PROJECT_ID" \
  --set-secrets "ANALYSIS_API_KEY=ANALYSIS_API_KEY:latest"

ANALYSIS_URL=$(gcloud run services describe $ANALYSIS_SERVICE \
  --region $REGION --platform managed --format 'value(status.url)')
echo ">>> analysis-pipeline URL: $ANALYSIS_URL"

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
  --set-env-vars "CRAWLER_SERVICE_URL=$CRAWLER_URL,ANALYSIS_SERVICE_URL=$ANALYSIS_URL" \
  --set-secrets "GOOGLE_CLIENT_ID=GOOGLE_CLIENT_ID:latest,GOOGLE_CLIENT_SECRET=GOOGLE_CLIENT_SECRET:latest,SECRET_KEY=FLASK_SECRET_KEY:latest,GENAI_API_KEY=GENAI_API_KEY:latest,CRAWLER_API_KEY=CRAWLER_API_KEY:latest,ANALYSIS_API_KEY=ANALYSIS_API_KEY:latest"

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
