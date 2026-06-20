# -*- coding: utf-8 -*-
"""
金鑰驗證（analysis-pipeline）

驗證順序：
  1. 系統金鑰（Secret Manager 注入的 ANALYSIS_API_KEY）— 常數時間比對。
  2. api_keys 白名單（content-analyser 核發，Firestore api_keys collection）—
     比對 SHA-256 hash、檢查 is_active 與 permissions。

此服務需要 'analyse' 權限。
"""
import hmac
import hashlib
from datetime import datetime, timezone

from firebase_admin import firestore

# 外部 api_keys 每日呼叫上限（成本防護／縱深防禦）。系統金鑰（產品自用）不受此限。
# 單把外部金鑰可於文件設 `daily_limit` 覆寫；未設則用此預設。
DEFAULT_KEY_DAILY_LIMIT = 1000


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _within_daily_quota(ref, data) -> bool:
    """外部金鑰每日配額（best-effort，非安全邊界）：超過上限回 False。
    以 UTC 日界重置；同一更新順帶累加當日計數與既有 call_count。"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    limit = data.get("daily_limit")
    if not isinstance(limit, int) or limit <= 0:
        limit = DEFAULT_KEY_DAILY_LIMIT
    used = data.get("quota_count", 0) if data.get("quota_day") == today else 0
    if used >= limit:
        return False
    try:
        ref.update({
            "last_used_at": firestore.SERVER_TIMESTAMP,
            "call_count": firestore.Increment(1),
            "quota_day": today,
            "quota_count": (used + 1),
        })
    except Exception:
        pass
    return True


# 系統金鑰（content-analyser，ANALYSIS_API_KEY）的呼叫者識別。
# content-analyser 自身已在它的 Firestore 綁 job↔project，故系統金鑰可讀所有 job。
SYSTEM_CALLER_ID = "system"


def authorize(provided_key: str, system_key: str,
              required_permission: str, db):
    """驗證並回傳呼叫者身分，供 job 歸屬檢查。

    回傳 (authorized: bool, caller_id: str | None)：
      - 系統金鑰（ANALYSIS_API_KEY）通過 → (True, "system")
      - 外部 api_keys（Firestore）通過 → (True, <該金鑰的 key_hash>)
      - 失敗 → (False, None)
    caller_id 用於 job owner 欄位：建立 job 時寫入，查詢時比對。
    """
    if system_key and provided_key and hmac.compare_digest(provided_key, system_key):
        return True, SYSTEM_CALLER_ID
    if not provided_key:
        return False, None
    if db is None:
        return False, None
    try:
        h = _hash_key(provided_key)
        docs = db.collection('api_keys').where('key_hash', '==', h).limit(1).stream()
        for d in docs:
            data = d.to_dict()
            if data.get('is_active') and required_permission in (data.get('permissions') or []):
                # 每日配額（成本防護）：超量則拒絕（系統金鑰已於上方提前放行，不受此限）。
                if not _within_daily_quota(d.reference, data):
                    print(f"[Auth] api_keys 已達每日配額上限，拒絕（{d.id}）", flush=True)
                    return False, None
                # 外部金鑰以 key_hash 為呼叫者識別（穩定、不洩漏原始金鑰）。
                return True, h
    except Exception as e:
        print(f"[Auth] api_keys 查詢失敗: {e}", flush=True)
    return False, None


def is_authorized(provided_key: str, system_key: str,
                  required_permission: str, db) -> bool:
    """向後相容包裝：僅回傳是否通過（不含呼叫者身分）。"""
    ok, _ = authorize(provided_key, system_key, required_permission, db)
    return ok
