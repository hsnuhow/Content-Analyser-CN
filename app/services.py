import os
import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud import secretmanager

# Initialize Firebase
# 在 Cloud Run 上，使用預設憑證 (不需 serviceAccountKey.json)
if not firebase_admin._apps:
    try:
        firebase_admin.initialize_app()
        print("[Firebase] Initialized with default credentials.")
    except Exception as e:
        print(f"[Firebase] Initialization failed: {e}")

db = firestore.client()

# Initialize Secret Manager
def get_secret(secret_id, project_id=None):
    """從 Secret Manager 讀取 Secret 的輔助函式"""
    if not project_id:
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    
    if not project_id:
        print("[SecretManager] Project ID not found.")
        return None

    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"

    try:
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8")
    except Exception as e:
        print(f"[SecretManager] Failed to access {secret_id}: {e}")
        return None

def set_secret(secret_id, payload, project_id=None):
    """更新 Secret 的輔助函式"""
    if not project_id:
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")

    client = secretmanager.SecretManagerServiceClient()
    parent = f"projects/{project_id}/secrets/{secret_id}"

    try:
        payload_bytes = payload.encode("UTF-8")
        client.add_secret_version(
            request={"parent": parent, "payload": {"data": payload_bytes}}
        )
        print(f"[SecretManager] Updated {secret_id}")
        return True
    except Exception as e:
        print(f"[SecretManager] Failed to update {secret_id}: {e}")
        return False


def get_admin_email():
    """從 Firestore system/config 讀取管理員 email。

    管理員身份不寫死在程式碼中，由 setup_admin.sh 一次性寫入。
    若尚未設定，回傳 None。
    """
    try:
        doc = db.collection('system').document('config').get()
        if doc.exists:
            return doc.to_dict().get('admin_email')
    except Exception as e:
        print(f"[Services] 無法讀取 admin_email: {e}")
    return None
