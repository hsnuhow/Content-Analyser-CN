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
from functools import wraps

from flask import session, redirect, url_for

from .services import get_admin_email, ensure_user


def is_dev_env() -> bool:
    return (os.environ.get('FLASK_DEBUG') == '1'
            or os.environ.get('FLASK_ENV') == 'development')


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

        # 補查白名單狀態（舊 session 或首次登入後尚未寫入 session 時）
        if 'whitelist_status' not in session:
            email = session['user'].get('email', '')
            session['whitelist_status'] = ensure_user(email)

        # 核心修正：非 approved 一律擋下，不得訪問受保護資源
        if session.get('whitelist_status') != 'approved':
            return redirect(url_for('main_bp.pending'))

        return f(*args, **kwargs)
    return decorated
