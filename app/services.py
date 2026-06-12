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


def get_admin_email() -> str | None:
    """從 Firestore system/config 讀取管理員 email。
    管理員身份不寫死在程式碼，由 setup_admin.sh 一次性寫入。
    """
    try:
        doc = db.collection('system').document('config').get()
        if doc.exists:
            return doc.to_dict().get('admin_email')
    except Exception as e:
        print(f"[Services] 無法讀取 admin_email: {e}")
    return None


def get_user(email: str) -> dict | None:
    """讀取 users/{email} 文件，不存在則回傳 None。"""
    try:
        doc = db.collection('users').document(email).get()
        return doc.to_dict() if doc.exists else None
    except Exception as e:
        print(f"[Services] 無法讀取用戶 {email}: {e}")
        return None


def ensure_user(email: str, display_name: str = "", picture: str = "") -> str:
    """確保 users/{email} 存在。首次登入時建立（status=pending）。
    回傳目前的 whitelist_status：'approved' | 'pending'。
    """
    admin_email = get_admin_email()
    # Admin 不需要白名單流程
    if admin_email and email.lower() == admin_email.lower():
        return "approved"

    ref = db.collection('users').document(email)
    try:
        doc = ref.get()
        if doc.exists:
            return doc.to_dict().get('whitelist_status', 'pending')
        # 首次登入 → 建立 pending 用戶
        ref.set({
            'email': email,
            'display_name': display_name,
            'picture': picture,
            'whitelist_status': 'pending',
            'added_by': None,
            'approved_at': None,
            'last_login': firestore.SERVER_TIMESTAMP,
            'created_at': firestore.SERVER_TIMESTAMP,
        })
        print(f"[Services] 新用戶 {email} 已建立（status=pending）")
        return 'pending'
    except Exception as e:
        print(f"[Services] ensure_user 失敗 {email}: {e}")
        return 'pending'


def update_last_login(email: str):
    """更新 users/{email}.last_login。"""
    try:
        db.collection('users').document(email).update({
            'last_login': firestore.SERVER_TIMESTAMP,
        })
    except Exception:
        pass


def approve_user(email: str, admin_email: str) -> bool:
    """將用戶設為 approved。"""
    try:
        db.collection('users').document(email).update({
            'whitelist_status': 'approved',
            'added_by': admin_email,
            'approved_at': firestore.SERVER_TIMESTAMP,
        })
        return True
    except Exception as e:
        print(f"[Services] approve_user 失敗 {email}: {e}")
        return False


def reject_user(email: str) -> bool:
    """將用戶設為 rejected（或刪除）。"""
    try:
        db.collection('users').document(email).update({
            'whitelist_status': 'rejected',
        })
        return True
    except Exception as e:
        print(f"[Services] reject_user 失敗 {email}: {e}")
        return False


def list_pending_users() -> list:
    """列出所有 pending 用戶。"""
    try:
        docs = db.collection('users').where('whitelist_status', '==', 'pending').stream()
        return [d.to_dict() for d in docs]
    except Exception as e:
        print(f"[Services] list_pending_users 失敗: {e}")
        return []


def list_all_users() -> list:
    """列出所有用戶（不含 system/config）。"""
    try:
        docs = db.collection('users').stream()
        return [d.to_dict() for d in docs]
    except Exception as e:
        print(f"[Services] list_all_users 失敗: {e}")
        return []
