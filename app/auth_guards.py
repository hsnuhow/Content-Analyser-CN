# -*- coding: utf-8 -*-
"""
共用驗證守衛（單一真實來源）

歷史問題：routes.py 與 project_routes.py 各自定義過一份 login_required，
其中 main_bp 的版本「補查 whitelist_status 卻不擋非 approved」，導致 pending/
rejected 用戶仍可訪問 main_bp 受保護頁面（如 /profile）。為杜絕分岔，統一在此。

提供：
  - is_dev_env()    判斷是否為本地開發環境（FLASK_DEBUG=1 或 FLASK_ENV=development）
  - login_required  已登入 + 白名單 approved 才放行；否則導向 /auth 或 /pending

⚠️ 安全注意：is_dev_env() 為 True 時 login_required 會以 admin 身分自動登入
（便於本地開發）。**正式環境（Cloud Run）絕不可設定 FLASK_DEBUG / FLASK_ENV。**
"""
import os
import time
from functools import wraps

from flask import session, redirect, url_for

from .services import get_admin_email, ensure_user

# 白名單狀態回查 TTL（秒）：避免每個請求都打 Firestore，但確保 admin 撤銷（reject）
# 最遲在此秒數內對既有 session 生效（不再無限信任登入時快取的 approved）。
WHITELIST_RECHECK_TTL = 60


def is_dev_env() -> bool:
    return (os.environ.get('FLASK_DEBUG') == '1'
            or os.environ.get('FLASK_ENV') == 'development')


def refresh_whitelist_status() -> str:
    """回查（帶 TTL 快取）目前登入者的白名單狀態並更新 session，回傳最新狀態。

    安全：解決「admin reject 後，既有 session 仍信任登入時快取的 approved」漏洞。
    每 WHITELIST_RECHECK_TTL 秒回查一次 Firestore；TTL 內沿用快取值（省讀取）。
    本地開發（is_dev_env）直接視為 approved，不回查（dev 帳號未必在 Firestore）。
    """
    if is_dev_env():
        session['whitelist_status'] = 'approved'
        return 'approved'
    email = (session.get('user') or {}).get('email', '')
    if not email:
        return session.get('whitelist_status', '')
    now = time.time()
    last = session.get('_wl_checked_at', 0)
    if 'whitelist_status' in session and (now - last) < WHITELIST_RECHECK_TTL:
        return session['whitelist_status']
    status = ensure_user(email)
    session['whitelist_status'] = status
    session['_wl_checked_at'] = now
    return status


def login_required(f):
    """要求：已登入（session 有 user）且白名單狀態為 approved。

    - 未登入：本地開發自動以 admin 登入；正式環境導向 /auth。
    - 已登入但非 approved（pending / rejected）：導向 /pending。
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            if is_dev_env():
                admin_email = get_admin_email() or os.environ.get('DEV_LOGIN_EMAIL', 'dev@localhost')
                print(f"[Auth] Dev 自動登入 {admin_email}", flush=True)
                session['user'] = {
                    'name': 'Developer',
                    'email': admin_email,
                    'picture': 'https://via.placeholder.com/150',
                }
                session['whitelist_status'] = 'approved'
                return f(*args, **kwargs)
            return redirect(url_for('main_bp.auth'))

        # 白名單狀態回查（帶 TTL）：撤銷及時生效，不再無限信任登入時快取的 approved。
        # 核心修正：非 approved 一律擋下，不得訪問受保護資源
        if refresh_whitelist_status() != 'approved':
            return redirect(url_for('main_bp.pending'))

        return f(*args, **kwargs)
    return decorated
