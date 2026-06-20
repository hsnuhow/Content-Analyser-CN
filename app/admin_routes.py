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
from .crawler_client import (check_crawler_health, cleanup_crawl_jobs,
                             submit_research, get_research_status)
from .analysis_client import (check_health as check_analysis_health,
                              cleanup_analysis_jobs, trigger_kb_index)
from . import kb_store
from firebase_admin import firestore


def _extract_text(file_storage) -> tuple:
    """從上傳檔抽純文字。支援 .md/.txt（解碼）與 .pdf（pypdf）。回 (text, mime)。"""
    fn = (file_storage.filename or '').lower()
    raw = file_storage.read()
    if fn.endswith('.pdf'):
        try:
            import io
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            text = "\n".join((p.extract_text() or '') for p in reader.pages)
            return text, 'application/pdf'
        except Exception as e:
            return '', f'pdf-error:{e}'
    # md / txt / 其他文字檔
    for enc in ('utf-8', 'utf-16', 'big5', 'gb18030'):
        try:
            return raw.decode(enc), 'text/plain'
        except Exception:
            continue
    return raw.decode('utf-8', errors='ignore'), 'text/plain'


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

        # 授權依據 = 登入身分等於 system/config.admin_email（唯一系統管理員，由 setup_admin 設定）。
        # 此身分本身即最高授權，故不另查 whitelist_status（admin 不走白名單流程，
        # ensure_user 對 admin 直接 approved）；以 email 完全比對為準。
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
# 字詞過濾清單（垃圾詞）編輯區 — 寫 Firestore system/config.term_filters
# analysis-pipeline 的 get_term_filters() 讀此清單（60s 快取）＋內建地板合併。
# 每個詞帶 scope（在哪種來源算垃圾）：全部 / 媒體 / 社群 / 論壇 / 影音 / 電商。
# ──────────────────────────────────────────────────────────────────────
_TERM_SCOPES = ('全部', '媒體', '社群', '論壇', '影音', '電商')


def _serialize_term_filters(entries) -> str:
    """term_filters 陣列 → 每行 `詞 | 範圍,範圍 | media` 的可編輯文字。"""
    lines = []
    for e in (entries or []):
        if not isinstance(e, dict):
            continue
        term = str(e.get('term', '')).strip()
        if not term:
            continue
        scopes = e.get('scope') or e.get('scopes') or ['全部']
        if isinstance(scopes, str):
            scopes = [scopes]
        line = f"{term} | {','.join(scopes)}"
        if e.get('type') == 'media':
            line += " | media"
        lines.append(line)
    return '\n'.join(lines)


def _parse_term_filters(text: str):
    """每行 `詞 | 範圍,範圍 | media` → term_filters 陣列（去重、驗證範圍）。"""
    out, seen = [], set()
    for raw in (text or '').splitlines():
        raw = raw.strip()
        if not raw or raw.startswith('#'):
            continue
        parts = [p.strip() for p in raw.split('|')]
        term = parts[0].strip()
        if not term or term in seen:
            continue
        seen.add(term)
        scopes = []
        if len(parts) > 1 and parts[1]:
            scopes = [s.strip() for s in parts[1].split(',')
                      if s.strip() in _TERM_SCOPES]
        if not scopes:
            scopes = ['全部']
        entry = {'term': term, 'scope': scopes}
        if len(parts) > 2 and 'media' in parts[2].lower():
            entry['type'] = 'media'
        out.append(entry)
    return out


@bp.route('/terms')
@admin_required
def term_filters():
    """字詞過濾清單編輯區。"""
    try:
        doc = db.collection('system').document('config').get()
        entries = (doc.to_dict() or {}).get('term_filters') if doc.exists else []
    except Exception:
        entries = []
    return render_template(
        'admin_terms.html', user=session.get('user'),
        terms_text=_serialize_term_filters(entries),
        scopes=_TERM_SCOPES, n_entries=len(entries or []))


@bp.route('/terms/save', methods=['POST'])
@admin_required
def term_filters_save():
    entries = _parse_term_filters(request.form.get('terms_text', ''))
    try:
        db.collection('system').document('config').set(
            {'term_filters': entries}, merge=True)
        flash(f'已儲存 {len(entries)} 個過濾詞（分析服務最多 60 秒生效）。', 'success')
    except Exception as e:
        flash(f'儲存失敗：{e}', 'danger')
    return redirect(url_for('admin_bp.term_filters'))


@bp.route('/terms/suggest')
@admin_required
def term_filters_suggest():
    """依某專案的爬蟲文本，回傳候選垃圾詞（三信號，呼叫分析服務）。"""
    pid = (request.args.get('pid') or '').strip()
    if not pid:
        return jsonify({'error': '缺少專案 ID'}), 400
    from .project_routes import _load_dataset_items
    contents = []
    try:
        ds_col = db.collection('projects').document(pid).collection('datasets')
        for ds in ds_col.stream():
            for it in _load_dataset_items(pid, ds.id):
                txt = it.get('content') or it.get('text')
                if txt and it.get('status', 'success') != 'failed':
                    contents.append({'url': it.get('url', ''),
                                     'title': it.get('title', ''), 'text': txt})
    except Exception as e:
        return jsonify({'error': f'讀取專案資料失敗：{e}'}), 500
    if not contents:
        return jsonify({'error': '此專案沒有可分析的爬蟲文本'}), 400
    from .analysis_client import suggest_filters as _sf
    return jsonify(_sf(contents))


@bp.route('/terms/suggest-all')
@admin_required
def term_filters_suggest_all():
    """全庫學習：聚合『所有專案』的爬蟲文本，一次找候選垃圾詞（優化引擎用）。
    比單一專案更準（跨來源歧異有更多論壇/影音樣本）。文本量大時取樣上限 MAX_DOCS。"""
    MAX_DOCS = 400
    # 效能：此處三層巢狀（每 project → 每 dataset → _load_dataset_items 各一次查詢），
    # dataset 總數無上限時 Firestore 讀取會爆量。除了既有 MAX_DOCS（文本筆數上限），
    # 另加 MAX_SCAN_DATASETS（掃描的 dataset 數上限）作雙重保護；達上限即停並標明為部分樣本。
    MAX_SCAN_DATASETS = 200
    from .project_routes import _load_dataset_items
    contents = []
    scanned_datasets = 0
    truncated = False
    try:
        for proj in db.collection('projects').stream():
            pid = proj.id
            for ds in (db.collection('projects').document(pid)
                       .collection('datasets').stream()):
                if scanned_datasets >= MAX_SCAN_DATASETS:
                    truncated = True
                    raise StopIteration
                scanned_datasets += 1
                for it in _load_dataset_items(pid, ds.id):
                    txt = it.get('content') or it.get('text')
                    if txt and it.get('status', 'success') != 'failed':
                        contents.append({'url': it.get('url', ''),
                                         'title': it.get('title', ''), 'text': txt})
                        if len(contents) >= MAX_DOCS:
                            truncated = True
                            raise StopIteration
    except StopIteration:
        pass
    except Exception as e:
        return jsonify({'error': f'讀取全庫資料失敗：{e}'}), 500
    if truncated:
        print(f"[term_suggest_all] 已達掃描上限（datasets={scanned_datasets}, "
              f"docs={len(contents)}），結果為部分樣本", flush=True)
    if not contents:
        return jsonify({'error': '全庫沒有可分析的爬蟲文本'}), 400
    from .analysis_client import suggest_filters as _sf
    res = _sf(contents, max_candidates=80, timeout=180)
    if isinstance(res, dict):
        label = f'全庫（{len(contents)} 篇）'
        if truncated:
            label += '・部分樣本（已達掃描上限）'
        res['scope_label'] = label
    return jsonify(res)


@bp.route('/terms/suggest/apply', methods=['POST'])
@admin_required
def term_filters_suggest_apply():
    """把勾選的候選詞（格式 `詞 | 範圍 | media`）併入現行 term_filters。"""
    picks = request.form.getlist('pick')
    new_entries = _parse_term_filters('\n'.join(picks))
    if not new_entries:
        flash('沒有勾選任何候選詞。', 'info')
        return redirect(url_for('admin_bp.term_filters'))
    try:
        doc = db.collection('system').document('config').get()
        existing = (doc.to_dict() or {}).get('term_filters') if doc.exists else []
        existing = existing or []
        have = {str(e.get('term', '')).strip() for e in existing if isinstance(e, dict)}
        added = [e for e in new_entries if e['term'] not in have]
        db.collection('system').document('config').set(
            {'term_filters': existing + added}, merge=True)
        flash(f'已加入 {len(added)} 個過濾詞（最多 60 秒生效）。', 'success')
    except Exception as e:
        flash(f'加入失敗：{e}', 'danger')
    return redirect(url_for('admin_bp.term_filters'))


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
    admin_email = (get_admin_email() or '').strip().lower()
    return render_template('admin_users.html',
                           user=session.get('user'), users=users,
                           admin_email=admin_email)


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

ALLOWED_SECRETS = [
    'GENAI_API_KEY',
    # Tier 3 住宅代理憑證（content-crawler 用；on/off 另由後台 Tier 3 toggle 控制）
    'PROXY_HOST', 'PROXY_PORT', 'PROXY_USER', 'PROXY_PASS', 'PROXY_PROVIDER',
]
# ⚠️ CRAWLER_API_KEY / ANALYSIS_API_KEY 刻意「不」開放後台編輯：它們是服務間共用的
#    驗證金鑰，隨手改一端會造成兩端不一致而中斷爬取/分析。輪換需同時更新 Secret Manager
#    並重部署「驗證方 + 呼叫方」兩個服務——交由維運腳本（rotate-key）統一處理，不走此表單。


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


@bp.route('/cleanup', methods=['POST'])
@admin_required
def cleanup_orphans():
    """清除孤兒資料：

    1. content-analyser 自身：刪除狀態異常或無記錄的暫存（此處主要清遠端 job 暫存層）。
    2. 呼叫 crawler / analysis 的 cleanup 端點，刪除已結束且超過 N 天的 job 文件。
    """
    try:
        days = max(0, int(request.form.get('days', 7)))
    except (TypeError, ValueError):
        days = 7

    crawl_res = cleanup_crawl_jobs(days)
    analysis_res = cleanup_analysis_jobs(days)

    parts = []
    if 'error' in crawl_res:
        parts.append(f'爬取任務清理失敗：{crawl_res["error"]}')
    else:
        parts.append(f'爬取任務清除 {crawl_res.get("deleted", 0)} 筆')
    if 'error' in analysis_res:
        parts.append(f'分析任務清理失敗：{analysis_res["error"]}')
    else:
        parts.append(f'分析任務清除 {analysis_res.get("deleted", 0)} 筆')

    has_err = 'error' in crawl_res or 'error' in analysis_res
    flash('；'.join(parts) + f'（門檻 {days} 天）。',
          'warning' if has_err else 'success')
    return redirect(url_for('admin_bp.admin_dashboard'))


# 估算單價統一由 app/pricing.py 提供（含正確 embedding 字元單價），admin 與專案頁共用。
from .pricing import est_cost_usd as _est_cost_usd, est_embed_cost_usd as _est_embed_cost_usd


def _aggregate_system_usage():
    """彙整 system_token_usage（系統付）：依 category/model 統計 token + embedding 字元 + 估算金額。"""
    by_category, by_model = {}, {}
    tot = {'prompt': 0, 'output': 0, 'total': 0, 'cost': 0.0}
    emb = {'chars': 0, 'n_texts': 0, 'cost': 0.0}
    n_jobs = 0
    try:
        for d in db.collection('system_token_usage').stream():
            r = d.to_dict() or {}
            n_jobs += 1
            for cat, v in (r.get('by_category') or {}).items():
                c = by_category.setdefault(cat, {'prompt': 0, 'output': 0, 'total': 0, 'calls': 0})
                for k in ('prompt', 'output', 'total', 'calls'):
                    c[k] += int(v.get(k, 0) or 0)
            tot['prompt'] += int(r.get('prompt_tokens', 0) or 0)
            tot['output'] += int(r.get('output_tokens', 0) or 0)
            tot['total'] += int(r.get('total_tokens', 0) or 0)
            e = r.get('embedding') or {}
            if e:
                emb['chars'] += int(e.get('chars', 0) or 0)
                emb['n_texts'] += int(e.get('n_texts', 0) or 0)
        # 估算金額：系統付以降噪模型（gemini-2.5-flash）估；embedding 以字元單價估（正確 ~$0.025/1M chars）
        tot['cost'] = _est_cost_usd('gemini-2.5-flash', tot['prompt'], tot['output'])
        emb['cost'] = _est_embed_cost_usd(emb['chars'])
    except Exception as e:
        print(f"[admin_usage] system_token_usage 彙整失敗：{e}", flush=True)
    return {'by_category': by_category, 'totals': tot, 'embedding': emb, 'n_jobs': n_jobs}


@bp.route('/usage')
@admin_required
def admin_usage():
    """使用量總覽：彙整各用戶 usage_log，依 action 統計次數與內容量。
    另含系統付 token 記帳（system_token_usage：降噪/embedding 等系統成本）。"""
    # 效能：usage_log 子集合會隨時間無限增長，原本無上限 stream() 整個子集合，
    # 用戶量×事件量大時 Firestore 讀取與記憶體都會爆量。改為每用戶只取最近 N 筆
    # （依 'at' SERVER_TIMESTAMP 由新到舊），彙總改為「近 N 筆樣本」。
    MAX_LOGS_PER_USER = 500
    summary = []
    recent = []
    try:
        for u in list_all_users():
            email = u.get('email')
            if not email:
                continue
            actions = {}
            total_count = 0
            try:
                logs = (db.collection('users').document(email)
                        .collection('usage_log')
                        .order_by('at', direction=firestore.Query.DESCENDING)
                        .limit(MAX_LOGS_PER_USER).stream())
            except Exception:
                logs = []
            n_events = 0
            for d in logs:
                rec = d.to_dict() or {}
                act = rec.get('action', 'unknown')
                cnt = rec.get('count', 1) or 0
                actions[act] = actions.get(act, 0) + 1
                total_count += cnt
                n_events += 1
                recent.append({
                    'email': email,
                    'action': act,
                    'detail': rec.get('detail', ''),
                    'count': cnt,
                    'at': rec.get('at'),
                })
            if n_events:
                summary.append({
                    'email': email,
                    'events': n_events,
                    'actions': actions,
                    'total_count': total_count,
                })
    except Exception as e:
        flash(f'讀取使用量失敗：{e}', 'danger')

    summary.sort(key=lambda s: s['events'], reverse=True)
    recent.sort(key=lambda r: r.get('at') or '', reverse=True)
    system_tokens = _aggregate_system_usage()
    return render_template('admin_usage.html',
                           user=session.get('user'),
                           summary=summary, recent=recent[:100],
                           system_tokens=system_tokens)


@bp.route('/force_kill_crawler', methods=['POST'])
@admin_required
def force_kill_crawler():
    flash(
        '爬蟲服務（content-crawler）為獨立 Cloud Run 服務。'
        '請至 GCP Cloud Run Console 重啟 content-crawler 服務。',
        'info'
    )
    return redirect(url_for('admin_bp.admin_dashboard'))


# ──────────────────────────────────────────────────────────────────────
# 選擇器研究候選確認（research tool 產出 → admin 確認後升級為主爬蟲知識）
# ──────────────────────────────────────────────────────────────────────
def _cand_doc_id(domain: str) -> str:
    # 與 crawler-service/site_learning._doc_id 一致（doc id 命名約定）
    return domain.replace('/', '_').replace('.', '_')[:200]


@bp.route('/selector-candidates')
@admin_required
def selector_candidates():
    """列出研究工具產出的候選選擇器，供 admin 確認升級或拒絕。"""
    pending, others = [], []
    try:
        for d in db.collection('selector_candidates').stream():
            c = d.to_dict() or {}
            (pending if c.get('status') == 'pending' else others).append(c)
    except Exception as e:
        flash(f'讀取候選失敗：{e}', 'danger')
    pending.sort(key=lambda c: c.get('proposed_at') or '', reverse=True)
    return render_template('admin_selector_candidates.html',
                           user=session.get('user'),
                           pending=pending, others=others,
                           active_research_job=session.get('_research_job'))


@bp.route('/selector-candidates/<path:domain>/approve', methods=['POST'])
@admin_required
def approve_selector_candidate(domain):
    """確認候選：把首選選擇器升級進 learned_selectors（主爬蟲執行時即讀取）。"""
    try:
        cref = db.collection('selector_candidates').document(_cand_doc_id(domain))
        snap = cref.get()
        if not snap.exists:
            flash(f'找不到候選：{domain}', 'danger')
            return redirect(url_for('admin_bp.selector_candidates'))
        c = snap.to_dict() or {}
        sels = c.get('selectors') or []
        if not sels:
            flash(f'候選無選擇器：{domain}', 'danger')
            return redirect(url_for('admin_bp.selector_candidates'))
        # per-domain 升級：寫 learned_selectors（與 crawler site_learning 同 collection/key）
        db.collection('learned_selectors').document(_cand_doc_id(domain)).set({
            'domain': domain, 'selector': sels[0],
            'chars': c.get('validated_chars', 0), 'cms': c.get('cms', ''),
            'updated_at': firestore.SERVER_TIMESTAMP, 'source': 'research_approved',
        }, merge=True)
        cref.set({'status': 'approved', 'approved_at': firestore.SERVER_TIMESTAMP}, merge=True)
        flash(f'✅ 已升級 {domain} → {sels[0]}（主爬蟲下次爬該網域即採用）', 'success')
    except Exception as e:
        flash(f'升級失敗：{e}', 'danger')
    return redirect(url_for('admin_bp.selector_candidates'))


@bp.route('/research-url', methods=['POST'])
@admin_required
def research_url():
    """主動研究指定 URL（不限失敗項；供測試/主動建模板）。"""
    raw = (request.form.get('urls', '') or '').strip()
    urls = [u.strip() for u in raw.replace(',', '\n').splitlines() if u.strip()]
    if not urls:
        flash('請輸入至少一個 URL。', 'danger')
        return redirect(url_for('admin_bp.selector_candidates'))
    result = submit_research(urls[:10])
    if 'error' in result:
        flash(f'啟動研究失敗：{result["error"]}', 'danger')
        return redirect(url_for('admin_bp.selector_candidates'))
    session['_research_job'] = result.get('job_id')
    flash(f'已啟動主動研究（{len(urls[:10])} 個 URL）。完成後此頁顯示候選/診斷。', 'success')
    return redirect(url_for('admin_bp.selector_candidates'))


@bp.route('/research-url/status')
@admin_required
def research_url_status():
    """輪詢主動研究結果（JSON）。"""
    job_id = request.args.get('job') or session.get('_research_job')
    if not job_id:
        return jsonify({'status': 'none'}), 200
    job = get_research_status(job_id)
    return jsonify({'status': job.get('status', 'unknown'),
                    'log': job.get('log', ''), 'result': job.get('result', {})}), 200


@bp.route('/research-url/clear', methods=['POST'])
@admin_required
def research_url_clear():
    """清除頁面上「主動研究指定 URL」的結果面板（清 session 鍵；候選清單不受影響）。"""
    session.pop('_research_job', None)
    flash('已清除主動研究結果面板。', 'success')
    return redirect(url_for('admin_bp.selector_candidates'))


@bp.route('/selector-candidates/<path:domain>/reject', methods=['POST'])
@admin_required
def reject_selector_candidate(domain):
    try:
        db.collection('selector_candidates').document(_cand_doc_id(domain)).set(
            {'status': 'rejected', 'rejected_at': firestore.SERVER_TIMESTAMP}, merge=True)
        flash(f'已拒絕候選：{domain}', 'warning')
    except Exception as e:
        flash(f'拒絕失敗：{e}', 'danger')
    return redirect(url_for('admin_bp.selector_candidates'))


# ──────────────────────────────────────────────────────────────────────
# 知識庫管理（延伸報告專家）：模型 A，啟用的專家 = 可產生的延伸報告類型
# ──────────────────────────────────────────────────────────────────────

@bp.route('/knowledge')
@admin_required
def knowledge_base():
    """知識庫專家清單（首次自動種子三專家）。"""
    kb_store.seed_default_experts()
    experts = kb_store.list_experts()
    for e in experts:
        e['documents'] = kb_store.list_documents(e['slug'])
    return render_template('admin_knowledge.html',
                           user=session.get('user'), experts=experts)


@bp.route('/knowledge/create', methods=['POST'])
@admin_required
def knowledge_create():
    ok, msg = kb_store.create_expert(
        slug=request.form.get('slug', ''),
        label=request.form.get('label', ''),
        prompt=request.form.get('prompt', ''),
        playbook=request.form.get('playbook', ''),
        enabled=(request.form.get('enabled') == '1'),
        order=int(request.form.get('order') or 99),
    )
    flash(msg, 'success' if ok else 'danger')
    return redirect(url_for('admin_bp.knowledge_base'))


@bp.route('/knowledge/<slug>/update', methods=['POST'])
@admin_required
def knowledge_update(slug):
    ok, msg = kb_store.update_expert(
        slug,
        label=request.form.get('label'),
        prompt=request.form.get('prompt'),
        playbook=request.form.get('playbook'),
        order=int(request.form.get('order') or 99),
    )
    flash(msg, 'success' if ok else 'danger')
    return redirect(url_for('admin_bp.knowledge_base'))


@bp.route('/knowledge/<slug>/toggle', methods=['POST'])
@admin_required
def knowledge_toggle(slug):
    enable = request.form.get('enable') == '1'
    ok, msg = kb_store.update_expert(slug, enabled=enable)
    flash(f'已{"啟用" if enable else "停用"}專家。' if ok else msg,
          'success' if ok else 'danger')
    return redirect(url_for('admin_bp.knowledge_base'))


@bp.route('/knowledge/<slug>/delete', methods=['POST'])
@admin_required
def knowledge_delete(slug):
    ok, msg = kb_store.delete_expert(slug)
    flash(msg, 'success' if ok else 'danger')
    return redirect(url_for('admin_bp.knowledge_base'))


# ── 知識庫參考文件（Phase 2：上傳/刪除/索引）──

@bp.route('/knowledge/<slug>/documents/upload', methods=['POST'])
@admin_required
def knowledge_doc_upload(slug):
    f = request.files.get('document')
    if not f or not f.filename:
        flash('請選擇檔案。', 'danger')
        return redirect(url_for('admin_bp.knowledge_base'))
    text, mime = _extract_text(f)
    if mime.startswith('pdf-error'):
        flash(f'PDF 抽取失敗：{mime}', 'danger')
        return redirect(url_for('admin_bp.knowledge_base'))
    ok, msg = kb_store.add_document(slug, f.filename, mime, text)
    if not ok:
        flash(msg, 'danger')
        return redirect(url_for('admin_bp.knowledge_base'))
    # 上傳後即重新索引（系統 SA embedding）
    res = trigger_kb_index(slug)
    if 'error' in res:
        flash(f'已上傳「{f.filename}」，但索引失敗：{res["error"]}（可稍後按重新索引）', 'warning')
    else:
        flash(f'已上傳「{f.filename}」並索引（{res.get("indexed", 0)} 塊）。', 'success')
    return redirect(url_for('admin_bp.knowledge_base'))


@bp.route('/knowledge/<slug>/documents/<doc_id>/delete', methods=['POST'])
@admin_required
def knowledge_doc_delete(slug, doc_id):
    ok, msg = kb_store.delete_document(slug, doc_id)
    if ok:
        res = trigger_kb_index(slug)  # 刪除後重建索引
        flash(f'已刪除文件並重建索引（{res.get("indexed", 0)} 塊）。'
              if 'error' not in res else f'已刪除文件，但索引失敗：{res["error"]}',
              'success' if 'error' not in res else 'warning')
    else:
        flash(msg, 'danger')
    return redirect(url_for('admin_bp.knowledge_base'))


@bp.route('/knowledge/<slug>/reindex', methods=['POST'])
@admin_required
def knowledge_reindex(slug):
    res = trigger_kb_index(slug)
    if 'error' in res:
        flash(f'索引失敗：{res["error"]}', 'danger')
    else:
        flash(f'已重新索引（{res.get("indexed", 0)} 塊）。', 'success')
    return redirect(url_for('admin_bp.knowledge_base'))
