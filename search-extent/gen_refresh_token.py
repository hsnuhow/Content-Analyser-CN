# -*- coding: utf-8 -*-
"""
產生 Google Ads API 的 OAuth refresh token（在你本機跑一次）。

此腳本不放進服務映像（見 .dockerignore），僅供初次設定用。

用法：
  # 1. 取出 client 憑證（你有讀取權；Claude 端被限制不讀 secret 值）
  export ADS_CLIENT_ID=$(gcloud secrets versions access latest --secret=ADS_CLIENT_ID)
  export ADS_CLIENT_SECRET=$(gcloud secrets versions access latest --secret=ADS_CLIENT_SECRET)
  # 2. 安裝依賴並執行
  pip install google-auth-oauthlib
  python3 search-extent/gen_refresh_token.py
  # 3. 瀏覽器會開啟，登入 how.penguin@gmail.com 並同意（scope: adwords）
  # 4. 終端機印出 refresh token 後，存入 Secret Manager：
  #    echo -n '<refresh_token>' | gcloud secrets create ADS_REFRESH_TOKEN --data-file=-
"""
import os
import sys

SCOPES = ["https://www.googleapis.com/auth/adwords"]


def main():
    client_id = os.environ.get("ADS_CLIENT_ID")
    client_secret = os.environ.get("ADS_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("請先設定環境變數 ADS_CLIENT_ID 與 ADS_CLIENT_SECRET。", file=sys.stderr)
        sys.exit(1)
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("請先安裝：pip install google-auth-oauthlib", file=sys.stderr)
        sys.exit(1)

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")

    print("\n===== refresh token（妥善保存、勿外流）=====")
    print(creds.refresh_token)
    print("\n存入 Secret Manager：")
    print("  echo -n '<上面的 refresh token>' | gcloud secrets create ADS_REFRESH_TOKEN --data-file=-")


if __name__ == "__main__":
    main()
