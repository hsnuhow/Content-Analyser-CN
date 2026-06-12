#!/bin/bash
set -e

# 取得目前的 GCP Project ID
PROJECT_ID=$(gcloud config get-value project)

if [ -z "$PROJECT_ID" ]; then
  echo "Error: Could not determine GCP Project ID."
  echo "Please run 'gcloud init' or 'gcloud config set project YOUR_PROJECT_ID' first."
  exit 1
fi

# 主程式（Web 應用）服務
SERVICE_NAME="content-analyser"
# 獨立爬蟲服務
CRAWLER_SERVICE_NAME="content-crawler"
REGION="asia-east1"

echo "========================================================"
echo "Deploying to Google Cloud Run (Web App + Independent Crawler)"
echo "Project        : $PROJECT_ID"
echo "Web Service    : $SERVICE_NAME"
echo "Crawler Service: $CRAWLER_SERVICE_NAME"
echo "Region         : $REGION"
echo "========================================================"
echo ""
echo "[前置需求] 請先在 Secret Manager 建立以下 secret："
echo "  - CRAWLER_API_KEY  (爬蟲 API 存取金鑰，例如: openssl rand -hex 32)"
echo "  - GENAI_API_KEY    (Gemini 金鑰，供低置信度 LLM 輔助)"
echo "  - GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / FLASK_SECRET_KEY (主程式用)"
echo ""

read -p "Do you want to continue? (y/N) " confirm
if [[ $confirm != [yY] && $confirm != [yY][eE][sS] ]]; then
  echo "Deployment cancelled."
  exit 0
fi

# ========================================================
# 1) 部署獨立爬蟲服務 (content-crawler)
# ========================================================
echo ""
echo ">>> [1/2] Building & deploying crawler service..."
gcloud builds submit crawler-service --tag gcr.io/$PROJECT_ID/$CRAWLER_SERVICE_NAME

# 爬蟲為重資源任務：較高記憶體與 CPU、較長 timeout。
# 受應用層 X-API-Key 保護，故維持 allow-unauthenticated（由主程式以金鑰呼叫）。
gcloud run deploy $CRAWLER_SERVICE_NAME \
  --image gcr.io/$PROJECT_ID/$CRAWLER_SERVICE_NAME \
  --platform managed \
  --region $REGION \
  --allow-unauthenticated \
  --memory 4Gi \
  --cpu 2 \
  --timeout 300 \
  --concurrency 4 \
  --clear-env-vars \
  --set-secrets "CRAWLER_API_KEY=CRAWLER_API_KEY:latest,GENAI_API_KEY=GENAI_API_KEY:latest"

# 取得爬蟲服務 URL，稍後注入主程式
CRAWLER_URL=$(gcloud run services describe $CRAWLER_SERVICE_NAME \
  --region $REGION --platform managed --format 'value(status.url)')
echo ">>> Crawler service URL: $CRAWLER_URL"

# ========================================================
# 2) 部署主程式 (content-analyser)，並注入爬蟲服務位址與金鑰
# ========================================================
echo ""
echo ">>> [2/2] Building & deploying web app..."
gcloud builds submit --tag gcr.io/$PROJECT_ID/$SERVICE_NAME

gcloud run deploy $SERVICE_NAME \
  --image gcr.io/$PROJECT_ID/$SERVICE_NAME \
  --platform managed \
  --region $REGION \
  --allow-unauthenticated \
  --memory 1Gi \
  --cpu 1 \
  --timeout 300 \
  --clear-env-vars \
  --set-env-vars "CRAWLER_SERVICE_URL=$CRAWLER_URL" \
  --set-secrets "GOOGLE_CLIENT_ID=GOOGLE_CLIENT_ID:latest,GOOGLE_CLIENT_SECRET=GOOGLE_CLIENT_SECRET:latest,SECRET_KEY=FLASK_SECRET_KEY:latest,GENAI_API_KEY=GENAI_API_KEY:latest,CRAWLER_API_KEY=CRAWLER_API_KEY:latest"

echo "========================================================"
echo "Deployment Complete!"
echo "Web App URL is shown above. Crawler URL: $CRAWLER_URL"
echo "IMPORTANT: Add the Web App URL + /callback to your Google OAuth Authorized Redirect URIs."
echo "========================================================"
