#!/bin/bash

# 取得目前的 GCP Project ID
PROJECT_ID=$(gcloud config get-value project)

if [ -z "$PROJECT_ID" ]; then
  echo "Error: Could not determine GCP Project ID."
  echo "Please run 'gcloud init' or 'gcloud config set project YOUR_PROJECT_ID' first."
  exit 1
fi

SERVICE_NAME="content-analyser"
REGION="asia-east1"
SECRET_PASSWORD="CONTENT_ANALYSER_ACCESS_KEY"
SECRET_APIKEY="GENAI_API_KEY"

echo "========================================================"
echo "Deploying to Google Cloud Run (Secure + AI + OAuth)"
echo "Project: $PROJECT_ID"
echo "Service: $SERVICE_NAME"
echo "Region : $REGION"
echo "Secrets : $SECRET_PASSWORD, $SECRET_APIKEY"
echo "========================================================"

# 詢問是否繼續
read -p "Do you want to continue? (y/N) " confirm
if [[ $confirm != [yY] && $confirm != [yY][eE][sS] ]]; then
  echo "Deployment cancelled."
  exit 0
fi

# 1. 提交建置到 Cloud Build
echo "Building container image..."
gcloud builds submit --tag gcr.io/$PROJECT_ID/$SERVICE_NAME

# 2. 部署到 Cloud Run
# [Fix] 提升 Cloud Run 資源配置 (記憶體和 CPU)
# [Security] 使用 Secret Manager 管理所有敏感資訊
echo "Deploying service..."
gcloud run deploy $SERVICE_NAME \
  --image gcr.io/$PROJECT_ID/$SERVICE_NAME \
  --platform managed \
  --region $REGION \
  --allow-unauthenticated \
  --memory 4Gi \
  --cpu 2 \
  --timeout 300 \
  --clear-env-vars \
  --set-secrets "GOOGLE_CLIENT_ID=GOOGLE_CLIENT_ID:latest,GOOGLE_CLIENT_SECRET=GOOGLE_CLIENT_SECRET:latest,SECRET_KEY=FLASK_SECRET_KEY:latest,GENAI_API_KEY=GENAI_API_KEY:latest"

echo "========================================================"
echo "Deployment Complete!"
echo "Service URL is shown above."
echo "IMPORTANT: Add this URL + /callback to your Google OAuth Authorized Redirect URIs."
