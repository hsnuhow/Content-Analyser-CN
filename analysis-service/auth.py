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

from firebase_admin import firestore


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def is_authorized(provided_key: str, system_key: str,
                  required_permission: str, db) -> bool:
    if system_key and provided_key and hmac.compare_digest(provided_key, system_key):
        return True
    if not provided_key:
        return False
    if db is None:
        return False
    try:
        h = _hash_key(provided_key)
        docs = db.collection('api_keys').where('key_hash', '==', h).limit(1).stream()
        for d in docs:
            data = d.to_dict()
            if data.get('is_active') and required_permission in (data.get('permissions') or []):
                try:
                    d.reference.update({
                        'last_used_at': firestore.SERVER_TIMESTAMP,
                        'call_count': firestore.Increment(1),
                    })
                except Exception:
                    pass
                return True
    except Exception as e:
        print(f"[Auth] api_keys 查詢失敗: {e}", flush=True)
    return False
