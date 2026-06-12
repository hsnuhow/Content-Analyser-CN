# -*- coding: utf-8 -*-
"""
管理員路由（Phase 0 清理版）

Phase 0 變更：
  - 移除 hardcode ADMIN_EMAIL，改從 Firestore system/config 讀取
  - force_kill_crawler 路由已正確處理（爬蟲為獨立服務）

Phase 3 將大幅重建：
  - 白名單用戶管理（審核 pending 用戶）
  - API 金鑰管理（核發 / 撤銷）
  - 服務健康監控（crawler + pipeline）
  - 使用量監控（按用戶）
"""
import os
from functools import wraps
from flask import Blueprint, render_template, session, redirect, url_for, request, flash
from .services import get_secret, set_secret, get_admin_email

bp = Blueprint('admin_bp', __name__, url_prefix='/admin')

ALLOWED_SECRETS = ['SYSTEM_GEMINI_KEY']


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = session.get('user')
        if not user:
            return redirect(url_for('main_bp.auth'))

        admin_email = get_admin_email()
        if not admin_email:
            return (
                "系統尚未設定管理員帳號。請執行 setup_admin.sh 完成初始化。",
                503
            )

        user_email = user.get('email', '').lower()
        if user_email != admin_email.lower():
            print(f"[Admin] Access Denied. User: {user_email}, Required: {admin_email}")
            return "Access Denied: You are not an administrator.", 403

        return f(*args, **kwargs)
    return decorated_function


@bp.route('/')
@admin_required
def admin_dashboard():
    system_key = get_secret('SYSTEM_GEMINI_KEY')
    key_status = "Configured" if system_key and system_key != "INITIAL_PLACEHOLDER_KEY" else "Not Configured"
    return render_template('admin_dashboard.html', user=session.get('user'), key_status=key_status)


@bp.route('/update_secrets', methods=['POST'])
@admin_required
def update_secrets():
    key_name = request.form.get('key_name')
    key_value = request.form.get('key_value')

    if not key_name or not key_value:
        flash('Error: Missing key name or value.', 'danger')
        return redirect(url_for('admin_bp.admin_dashboard'))

    if key_name not in ALLOWED_SECRETS:
        flash(f'Error: Updating secret "{key_name}" is not allowed via this panel.', 'danger')
        return redirect(url_for('admin_bp.admin_dashboard'))

    success = set_secret(key_name, key_value)
    if success:
        flash(f'Successfully updated {key_name}.', 'success')
    else:
        flash(f'Failed to update {key_name}. Please check system logs.', 'danger')

    return redirect(url_for('admin_bp.admin_dashboard'))


@bp.route('/force_kill_crawler', methods=['POST'])
@admin_required
def force_kill_crawler():
    """爬蟲已是獨立 Cloud Run 服務，主程式不執行 Chrome。
    如需重置爬蟲服務，請於 Cloud Run Console 重啟 content-crawler。
    """
    flash(
        '爬蟲服務（content-crawler）為獨立 Cloud Run 服務，主程式不執行 Chrome。'
        '請至 GCP Cloud Run Console 重啟 content-crawler 服務。',
        'info'
    )
    return redirect(url_for('admin_bp.admin_dashboard'))
