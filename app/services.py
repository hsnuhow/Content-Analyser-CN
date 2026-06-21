import os
import firebase_admin
from firebase_admin import firestore
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
def _resolve_project_id() -> str | None:
    """多來源解析 GCP Project ID，不只依賴環境變數。
    Cloud Run 未必注入 GOOGLE_CLOUD_PROJECT，故再退回 firebase app /
    application default credentials 取得，避免 set_secret 出現 projects/None。
    """
    pid = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT")
    if pid:
        return pid
    try:
        pid = firebase_admin.get_app().project_id
        if pid:
            return pid
    except Exception:
        pass
    try:
        import google.auth
        _, pid = google.auth.default()
        return pid
    except Exception:
        return None


def get_secret(secret_id, project_id=None):
    """從 Secret Manager 讀取 Secret 的輔助函式"""
    if not project_id:
        project_id = _resolve_project_id()

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
        project_id = _resolve_project_id()

    if not project_id:
        print("[SecretManager] Project ID not found（無法更新 secret）。")
        return False

    from google.api_core.exceptions import NotFound

    client = secretmanager.SecretManagerServiceClient()
    parent = f"projects/{project_id}/secrets/{secret_id}"
    payload_bytes = payload.encode("UTF-8")

    try:
        client.add_secret_version(
            request={"parent": parent, "payload": {"data": payload_bytes}}
        )
        print(f"[SecretManager] Updated {secret_id}")
        return True
    except NotFound:
        # secret 尚未存在 → 自動建立後再加首個版本（首次設定可純後台完成，免 gcloud）。
        # 需 service account 具 secretmanager.secrets.create 權限；無權限時走 except 回傳 False。
        try:
            client.create_secret(request={
                "parent": f"projects/{project_id}",
                "secret_id": secret_id,
                "secret": {"replication": {"automatic": {}}},
            })
            client.add_secret_version(
                request={"parent": parent, "payload": {"data": payload_bytes}}
            )
            print(f"[SecretManager] Created + set {secret_id}")
            return True
        except Exception as e:
            print(f"[SecretManager] Failed to create {secret_id}: {e}")
            return False
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
    email = email.strip().lower()
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
    email = email.strip().lower()
    admin_email = get_admin_email()
    # Admin 不需要白名單流程，但仍維護一份完整的 user 文件（讓管理員有正確 email 記錄，
    # 並自動修復早期建立、缺欄位的畸形文件）。display_name/picture 為空時不覆蓋既有值。
    if admin_email and email == admin_email.strip().lower():
        try:
            payload = {
                'email': email,
                'whitelist_status': 'approved',
                'is_admin': True,
                'last_login': firestore.SERVER_TIMESTAMP,
            }
            if display_name:
                payload['display_name'] = display_name
            if picture:
                payload['picture'] = picture
            db.collection('users').document(email).set(payload, merge=True)
        except Exception as e:
            print(f"[Services] 維護 admin 文件失敗 {email}: {e}")
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
    email = email.strip().lower()
    try:
        db.collection('users').document(email).update({
            'last_login': firestore.SERVER_TIMESTAMP,
        })
    except Exception:
        pass


def approve_user(email: str, admin_email: str) -> bool:
    """將用戶設為 approved。"""
    email = email.strip().lower()
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
    email = email.strip().lower()
    try:
        db.collection('users').document(email).update({
            'whitelist_status': 'rejected',
        })
        return True
    except Exception as e:
        print(f"[Services] reject_user 失敗 {email}: {e}")
        return False


def _user_dict_with_email(d) -> dict:
    """把 doc 轉 dict，並以 document ID 補上 email（users/{email}，doc ID 即權威 email）。
    早期/畸形文件可能缺 email 欄位；統一在此補齊，避免 url_for 取到 None。"""
    u = d.to_dict() or {}
    u['email'] = d.id
    return u


def list_pending_users() -> list:
    """列出所有 pending 用戶。"""
    try:
        docs = db.collection('users').where('whitelist_status', '==', 'pending').stream()
        return [_user_dict_with_email(d) for d in docs]
    except Exception as e:
        print(f"[Services] list_pending_users 失敗: {e}")
        return []


def list_all_users() -> list:
    """列出所有用戶（不含 system/config）。"""
    try:
        docs = db.collection('users').stream()
        return [_user_dict_with_email(d) for d in docs]
    except Exception as e:
        print(f"[Services] list_all_users 失敗: {e}")
        return []


# ──────────────────────────────────────────────────────────────────────
# API 金鑰管理（api_keys collection）
#
# 供外部工具（Colab / Claude Cowork）呼叫 content-crawler / analysis-pipeline。
# 金鑰明文只在核發時顯示一次；Firestore 只存 SHA-256 hash。
# crawler 與 analysis 服務各自驗證（查同一個 api_keys collection）。
# ──────────────────────────────────────────────────────────────────────
import hashlib
import secrets

API_KEY_PREFIX = "iok"  # InsightOut Key
VALID_PERMISSIONS = ("crawl", "analyse")


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def create_api_key(name: str, permissions: list, created_by: str) -> dict:
    """核發新 API 金鑰。回傳 {key_id, raw_key, ...}。
    raw_key 為明文，只在此回傳一次，不存 Firestore。
    """
    perms = [p for p in permissions if p in VALID_PERMISSIONS]
    if not perms:
        perms = list(VALID_PERMISSIONS)

    raw_key = f"{API_KEY_PREFIX}_{secrets.token_hex(24)}"
    key_hash = _hash_key(raw_key)
    key_prefix = raw_key[:12]  # 顯示用（iok_xxxxxx）

    ref = db.collection('api_keys').document()
    ref.set({
        'key_id': ref.id,
        'name': name,
        'key_hash': key_hash,
        'key_prefix': key_prefix,
        'permissions': perms,
        'created_by': created_by,
        'created_at': firestore.SERVER_TIMESTAMP,
        'last_used_at': None,
        'is_active': True,
        'call_count': 0,
    })
    return {
        'key_id': ref.id,
        'raw_key': raw_key,
        'name': name,
        'permissions': perms,
        'key_prefix': key_prefix,
    }


def list_api_keys() -> list:
    """列出所有 API 金鑰（不含明文，只有 hash/prefix）。"""
    try:
        docs = db.collection('api_keys').stream()
        return [d.to_dict() for d in docs]
    except Exception as e:
        print(f"[Services] list_api_keys 失敗: {e}")
        return []


def revoke_api_key(key_id: str) -> bool:
    """撤銷金鑰（is_active=False）。"""
    try:
        db.collection('api_keys').document(key_id).update({'is_active': False})
        return True
    except Exception as e:
        print(f"[Services] revoke_api_key 失敗 {key_id}: {e}")
        return False


def reactivate_api_key(key_id: str) -> bool:
    """重新啟用金鑰。"""
    try:
        db.collection('api_keys').document(key_id).update({'is_active': True})
        return True
    except Exception as e:
        print(f"[Services] reactivate_api_key 失敗 {key_id}: {e}")
        return False
