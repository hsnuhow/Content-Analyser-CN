# -*- coding: utf-8 -*-
"""
管理員路由（Phase 3）

Blueprint：admin_bp（prefix /admin）

功能：
  /admin/              → 控制台首頁（服務健康狀態）
  /admin/users         → 白名單用戶管理（approve / reject）
  /admin/api-keys      → API 金鑰狀態說明（實際管理由 Secret Manager 完成）
  /admin/update_secrets → 更新 Secret Manager 中的 secrets（原有功能保留）
"""
import os
from functools import wraps
from flask import (Blueprint, render_template, session, redirect,
                   url_for, request, flash, jsonify)

from .services import (
    get_secret, set_secret, get_admin_email,
    list_all_users, approve_user, reject_user,
)
from .crawler_client import check_crawler_health
from .analysis_client import check_health as check_analysis_health

bp = Blueprint('admin_bp', __name__, url_prefix='/admin')


# ──────────────────────────────────────────────────────────────────────
# Admin 保護
# ──────────────────────────────────────────────────────────────────────

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = session.get('user')
        if not user:
            return redirect(url_for('main_bp.auth'))

        admin_email = get_admin_email()
        if not admin_email:
            return "系統尚未設定管理員帳號。請執行 setup_admin.sh 完成初始化。", 503

        if user.get('email', '').lower() != admin_email.lower():
            return "Access Denied: You are not an administrator.", 403

        return f(*args, **kwargs)
    return decorated


# ──────────────────────────────────────────────────────────────────────
# 控制台
# ──────────────────────────────────────────────────────────────────────

@bp.route('/')
@admin_required
def admin_dashboard():
    crawler_health = check_crawler_health()
    analysis_health = check_analysis_health()

    pending_users = [
        u for u in list_all_users()
        if u.get('whitelist_status') == 'pending'
    ]

    return render_template(
        'admin_dashboard.html',
        user=session.get('user'),
        crawler_health=crawler_health,
        analysis_health=analysis_health,
        pending_count=len(pending_users),
    )


# ──────────────────────────────────────────────────────────────────────
# 用戶白名單管理
# ──────────────────────────────────────────────────────────────────────

@bp.route('/users')
@admin_required
def admin_users():
    users = list_all_users()
    # 按狀態排序：pending 先，再按 email
    users.sort(key=lambda u: (
        0 if u.get('whitelist_status') == 'pending' else 1,
        u.get('email', '')
    ))
    return render_template('admin_users.html',
                           user=session.get('user'), users=users)


@bp.route('/users/<email>/approve', methods=['POST'])
@admin_required
def approve_user_route(email):
    admin_email = get_admin_email()
    if approve_user(email, admin_email):
        flash(f'✅ 已批准 {email}', 'success')
    else:
        flash(f'❌ 批准失敗：{email}', 'danger')
    return redirect(url_for('admin_bp.admin_users'))


@bp.route('/users/<email>/reject', methods=['POST'])
@admin_required
def reject_user_route(email):
    if reject_user(email):
        flash(f'已拒絕/停用 {email}', 'warning')
    else:
        flash(f'操作失敗：{email}', 'danger')
    return redirect(url_for('admin_bp.admin_users'))


# ──────────────────────────────────────────────────────────────────────
# Secret Manager 管理（原有功能保留）
# ──────────────────────────────────────────────────────────────────────

ALLOWED_SECRETS = ['GENAI_API_KEY', 'CRAWLER_API_KEY', 'ANALYSIS_API_KEY']


@bp.route('/update_secrets', methods=['POST'])
@admin_required
def update_secrets():
    key_name = request.form.get('key_name', '').strip()
    key_value = request.form.get('key_value', '').strip()

    if not key_name or not key_value:
        flash('請填寫 secret 名稱與值。', 'danger')
        return redirect(url_for('admin_bp.admin_dashboard'))

    if key_name not in ALLOWED_SECRETS:
        flash(f'不允許透過此介面更新 "{key_name}"。', 'danger')
        return redirect(url_for('admin_bp.admin_dashboard'))

    if set_secret(key_name, key_value):
        flash(f'✅ {key_name} 已更新。新值將於下次 Cloud Run 重啟後生效。', 'success')
    else:
        flash(f'更新 {key_name} 失敗，請查看系統日誌。', 'danger')

    return redirect(url_for('admin_bp.admin_dashboard'))


@bp.route('/force_kill_crawler', methods=['POST'])
@admin_required
def force_kill_crawler():
    flash(
        '爬蟲服務（content-crawler）為獨立 Cloud Run 服務。'
        '請至 GCP Cloud Run Console 重啟 content-crawler 服務。',
        'info'
    )
    return redirect(url_for('admin_bp.admin_dashboard'))
