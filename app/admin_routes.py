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
    db, get_secret, set_secret, get_admin_email,
    list_all_users, approve_user, reject_user,
    create_api_key, list_api_keys, revoke_api_key, reactivate_api_key,
)
from .crawler_client import check_crawler_health
from .analysis_client import check_health as check_analysis_health


def _get_tier3_enabled() -> bool:
    """讀 Firestore system/config.tier3_enabled（爬蟲 Tier 3 代理開關），預設 False。"""
    try:
        doc = db.collection('system').document('config').get()
        return bool(doc.exists and doc.to_dict().get('tier3_enabled'))
    except Exception:
        return False

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
        tier3_enabled=_get_tier3_enabled(),
    )


@bp.route('/tier3-toggle', methods=['POST'])
@admin_required
def tier3_toggle():
    """切換爬蟲 Tier 3 代理開關（寫 Firestore system/config.tier3_enabled）。

    crawler 端 load_proxy_config 會讀此 flag（60s 快取），不必重建 revision。
    注意：開啟後仍需 crawler env 有代理憑證（PROXY_HOST/PORT/USER/PASS）才實際生效。
    """
    enable = request.form.get('enable') == '1'
    try:
        db.collection('system').document('config').set(
            {'tier3_enabled': enable}, merge=True)
        flash(f'Tier 3 代理已{"開啟" if enable else "關閉"}（最多 60 秒生效）。', 'success')
    except Exception as e:
        flash(f'切換失敗：{e}', 'danger')
    return redirect(url_for('admin_bp.admin_dashboard'))


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
# API 金鑰管理（供 Colab / Claude Cowork 呼叫 crawler / analysis）
# ──────────────────────────────────────────────────────────────────────

@bp.route('/api-keys')
@admin_required
def admin_api_keys():
    keys = list_api_keys()
    keys.sort(key=lambda k: k.get('created_at') or '', reverse=True)
    # 服務 URL（供 Colab 呼叫範例顯示）
    crawler_url = os.environ.get('CRAWLER_SERVICE_URL', '')
    analysis_url = os.environ.get('ANALYSIS_SERVICE_URL', '')
    # 若上一動作剛核發金鑰，明文透過 flash 的 session 暫存顯示
    new_key = session.pop('_new_api_key', None)
    return render_template('admin_api_keys.html',
                           user=session.get('user'), keys=keys,
                           crawler_url=crawler_url, analysis_url=analysis_url,
                           new_key=new_key)


@bp.route('/api-keys/create', methods=['POST'])
@admin_required
def create_api_key_route():
    name = request.form.get('name', '').strip()
    perms = request.form.getlist('permissions')  # ['crawl', 'analyse']
    if not name:
        flash('請填寫金鑰名稱。', 'danger')
        return redirect(url_for('admin_bp.admin_api_keys'))
    if not perms:
        flash('請至少選擇一個權限。', 'danger')
        return redirect(url_for('admin_bp.admin_api_keys'))

    result = create_api_key(name, perms, get_admin_email())
    # 明文金鑰只顯示一次，透過 session 暫存帶到下一頁
    session['_new_api_key'] = {
        'name': result['name'],
        'raw_key': result['raw_key'],
        'permissions': result['permissions'],
    }
    flash(f'✅ 已核發金鑰「{name}」，請立即複製（只顯示一次）。', 'success')
    return redirect(url_for('admin_bp.admin_api_keys'))


@bp.route('/api-keys/<key_id>/revoke', methods=['POST'])
@admin_required
def revoke_api_key_route(key_id):
    if revoke_api_key(key_id):
        flash('已撤銷金鑰。', 'warning')
    else:
        flash('撤銷失敗。', 'danger')
    return redirect(url_for('admin_bp.admin_api_keys'))


@bp.route('/api-keys/<key_id>/reactivate', methods=['POST'])
@admin_required
def reactivate_api_key_route(key_id):
    if reactivate_api_key(key_id):
        flash('已重新啟用金鑰。', 'success')
    else:
        flash('操作失敗。', 'danger')
    return redirect(url_for('admin_bp.admin_api_keys'))


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
