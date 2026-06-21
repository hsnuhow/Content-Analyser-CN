# -*- coding: utf-8 -*-
"""分析領域路由：提交分析/報告檢視/狀態輪詢/下載/CSV/延伸行動報告（derive）/整合分析（combined）。
註冊在 project_bp（package __init__ 於底部 import 本模組以套用 @bp.route）。"""
import json
import re
from io import BytesIO
from flask import (request, jsonify, flash, redirect, url_for, send_file, abort,
                   render_template)
from firebase_admin import firestore

from . import bp, project_access_required, current_user_email, get_project, log_usage
from ..services import db
from ..analysis_client import (submit_analysis, submit_image_analysis, submit_combined,
                              submit_audience, cancel_analysis)
from .. import kb_store
from ..analysis_store import (_analysis_ref, _reconcile_analysis, _reconcile_derive,
                             _derived_label)
from ..datasets_store import _load_dataset_items

@bp.route('/<pid>/analyses', methods=['POST'])
@project_access_required(min_role='editor')
def submit_analysis_route(pid, project, role):
    """提交分析任務。

    表單欄位：
      report_title  報告標題
      contents_json JSON 陣列（[{url,title,text,source_type}, ...]）
    """
    report_title = request.form.get('report_title', '').strip()
    contents_raw = request.form.get('contents_json', '').strip()

    if not report_title:
        flash('請填寫報告標題。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    try:
        contents = json.loads(contents_raw)
        if not isinstance(contents, list) or not contents:
            raise ValueError('contents 必須是非空陣列')
        if len(contents) > 100:
            raise ValueError('每次最多 100 篇內容')
        _VALID_SOURCE_TYPES = {'media', 'ecommerce', 'forum', 'dcard', 'youtube', 'direct', ''}
        truncated_count = 0
        for i, item in enumerate(contents):
            if not isinstance(item, dict):
                raise ValueError(f'第 {i+1} 筆內容格式錯誤')
            url = str(item.get('url', ''))[:2048]
            title = str(item.get('title', ''))[:512]
            raw_text = str(item.get('text') or item.get('content') or '')
            if len(raw_text) > 50000:
                truncated_count += 1
            text = raw_text[:50000]
            src = str(item.get('source_type', ''))
            if src not in _VALID_SOURCE_TYPES:
                src = ''
            contents[i] = {'url': url, 'title': title, 'text': text, 'source_type': src}
        if truncated_count > 0:
            flash(f'注意：有 {truncated_count} 篇文章超過 50,000 字元，已截斷後送出分析。', 'warning')
    except Exception as e:
        flash(f'內容格式錯誤：{e}。請貼入正確的 JSON 陣列。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    llm_config = project.get('llm_config', {})
    llm_api_key = llm_config.get('api_key', '')
    if not llm_api_key:
        flash('尚未設定 LLM API Key，請先至專案設定填入。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    # 呼叫 analysis-pipeline
    result = submit_analysis(
        report_title=report_title,
        contents=contents,
        llm_provider=llm_config.get('provider', 'gemini'),
        llm_model=llm_config.get('model', 'gemini-2.5-flash'),
        llm_api_key=llm_api_key,
        temperature=llm_config.get('temperature', 0.3),
        thinking=llm_config.get('thinking', False),
        search_extent=llm_config.get('search_extent', True),
        max_output_tokens=llm_config.get('max_output_tokens', 8192),
        top_p=llm_config.get('top_p'),
        input_scale=llm_config.get('input_scale', 'standard'),
    )

    if 'error' in result:
        flash(f'提交失敗：{result["error"]}', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    job_id = result.get('job_id')

    # 在 Firestore 建立分析記錄
    analysis_ref = (
        db.collection('projects').document(pid)
        .collection('analyses').document()
    )
    analysis_ref.set({
        'id': analysis_ref.id,
        'job_id': job_id,
        'report_title': report_title,
        'status': 'pending',
        'progress': 0,
        'log': '任務已提交，等待分析引擎處理...',
        'n_articles': len(contents),
        'llm_provider': llm_config.get('provider', 'gemini'),
        'llm_model': llm_config.get('model', 'gemini-2.5-flash'),
        'submitted_by': current_user_email(),
        'submitted_at': firestore.SERVER_TIMESTAMP,
        'completed_at': None,
        'result_markdown': None,
    })

    log_usage('analyse', detail=report_title, count=len(contents), project_id=pid)
    flash(f'分析任務已提交（{len(contents)} 篇），正在處理中...', 'success')
    return redirect(url_for('project_bp.analysis_detail',
                            pid=pid, aid=analysis_ref.id))


# 分析狀態對帳層已抽出（見 analysis_store.py）。
from ..analysis_store import _analysis_ref, _reconcile_analysis, _reconcile_derive, _derived_label  # noqa: F401

@bp.route('/<pid>/analyses/<aid>')
@project_access_required(min_role='viewer')
def analysis_detail(pid, aid, project, role):
    """查看報告（若已完成）或顯示進度。"""
    doc = (db.collection('projects').document(pid)
           .collection('analyses').document(aid).get())
    if not doc.exists:
        abort(404)
    analysis = doc.to_dict() | {'id': aid}
    # lazy 自癒（載入時對帳，不靠前端輪詢硬撐）：主分析 + 延伸報告各自補寫
    try:
        _reconcile_analysis(pid, aid, analysis)
    except Exception as e:
        print(f"[analysis] 對帳略過：{e}", flush=True)
    if analysis.get('derive_status') == 'running' and analysis.get('derive_job_id'):
        try:
            _reconcile_derive(pid, aid, analysis)
        except Exception as e:
            print(f"[derive] 對帳略過：{e}", flush=True)
    return render_template('analysis_detail.html',
                           project=project, pid=pid,
                           analysis=analysis, role=role)


@bp.route('/<pid>/analyses/<aid>/status')
@project_access_required(min_role='viewer')
def analysis_status(pid, aid, project, role):
    """輪詢分析進度（JSON）。前端每 3 秒呼叫一次。"""
    doc = (db.collection('projects').document(pid)
           .collection('analyses').document(aid).get())
    if not doc.exists:
        return jsonify({'error': '找不到此分析任務'}), 404
    return jsonify(_reconcile_analysis(pid, aid, doc.to_dict()))


@bp.route('/<pid>/analyses/<aid>/download')
@project_access_required(min_role='viewer')
def download_analysis(pid, aid, project, role):
    """下載分析報告（.md 檔案）。"""
    doc = (db.collection('projects').document(pid)
           .collection('analyses').document(aid).get())
    if not doc.exists:
        abort(404)

    analysis = doc.to_dict()
    if analysis.get('status') != 'completed':
        flash('報告尚未完成，無法下載。', 'warning')
        return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=aid))

    markdown = analysis.get('result_markdown', '')
    raw_title = analysis.get('report_title', 'report')
    filename = re.sub(r'[^\w\-. ]', '_', raw_title).strip()[:80] + '.md'

    stream = BytesIO(markdown.encode('utf-8'))
    return send_file(
        stream,
        as_attachment=True,
        download_name=filename,
        mimetype='text/markdown; charset=utf-8',
    )


# 三項數值分析的 CSV 下載（核實用）。kind 白名單對應 numeric_exports 的鍵。
_NUMERIC_EXPORT_KINDS = {
    'tfidf': 'TF-IDF關鍵字',
    'association': '關聯規則',
    'entities': '實體情感',
}


@bp.route('/<pid>/analyses/<aid>/download/<kind>.csv')
@project_access_required(min_role='viewer')
def download_analysis_csv(pid, aid, project, role, kind):
    """下載單項數值分析結果的 CSV（tfidf / association / entities）。"""
    if kind not in _NUMERIC_EXPORT_KINDS:
        abort(404)
    doc = (db.collection('projects').document(pid)
           .collection('analyses').document(aid).get())
    if not doc.exists:
        abort(404)

    analysis = doc.to_dict()
    if analysis.get('status') != 'completed':
        flash('報告尚未完成，無法下載。', 'warning')
        return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=aid))

    exports = analysis.get('numeric_exports') or {}
    csv_text = exports.get(kind)
    if not csv_text:
        flash('此報告沒有數值匯出檔（可能是舊報告），請重新分析以產生。', 'warning')
        return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=aid))

    raw_title = analysis.get('report_title', 'report')
    base = re.sub(r'[^\w\-. ]', '_', raw_title).strip()[:60]
    filename = f"{base}_{kind}.csv"
    # utf-8-sig（含 BOM）讓 Excel 正確顯示中文
    stream = BytesIO(csv_text.encode('utf-8-sig'))
    return send_file(
        stream,
        as_attachment=True,
        download_name=filename,
        mimetype='text/csv; charset=utf-8',
    )


# ── 延伸行動報告：模型 A，啟用的知識庫專家 = 可產生的延伸報告類型 ──
# 分析師於主報告完成並認可後手動觸發。唯讀主報告、結果存母分析 analyses/{aid}.derived_reports
# （綁定該主報告；主報告換＝新 aid＝重產）。生成用「用戶專案的 LLM Key」，系統不負擔生成成本。

@bp.route('/<pid>/analyses/<aid>/derive', methods=['POST'])
@project_access_required(min_role='editor')
def derive_audience_reports(pid, aid, project, role):
    """觸發產生延伸行動報告（非同步），依後台啟用的知識庫專家。文字分析、completed 才可。"""
    doc = _analysis_ref(pid, aid).get()
    if not doc.exists:
        abort(404)
    analysis = doc.to_dict()
    if analysis.get('status') != 'completed' or not analysis.get('result_markdown'):
        flash('主報告尚未完成，無法產生延伸報告。', 'warning')
        return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=aid))
    if analysis.get('kind') == 'visual':
        flash('視覺報告不支援延伸行動報告。', 'warning')
        return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=aid))

    experts = kb_store.list_enabled_experts()
    if not experts:
        flash('知識庫尚無啟用的專家，請先至 /admin/knowledge 建立並啟用。', 'warning')
        return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=aid))

    llm = project.get('llm_config', {}) or {}
    api_key = llm.get('api_key', '')
    if not api_key:
        flash('專案尚未設定 LLM API Key，無法產生延伸報告。請至專案設定填入。', 'danger')
        return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=aid))

    payload_experts = [
        {'slug': e['slug'], 'label': e.get('label', e['slug']),
         'prompt': e.get('prompt', ''), 'playbook': e.get('playbook', '')}
        for e in experts
    ]
    res = submit_audience(
        report_title=analysis.get('report_title', '報告'),
        source_markdown=analysis.get('result_markdown', ''),
        experts=payload_experts,
        llm_provider=llm.get('provider', 'gemini'),
        llm_model=llm.get('model', 'gemini-2.5-flash'),
        llm_api_key=api_key,
    )
    job_id = res.get('job_id')
    if not job_id:
        flash(f"延伸報告提交失敗：{res.get('error', '未知錯誤')}", 'danger')
        return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=aid))

    # 存當下啟用的專家（slug+label），供報告頁顯示標題（與動態 slug 對應）
    _analysis_ref(pid, aid).update({
        'derive_job_id': job_id,
        'derive_status': 'running',
        'derive_experts': [{'slug': e['slug'], 'label': e.get('label', e['slug'])}
                           for e in experts],
        'derive_error': firestore.DELETE_FIELD,
    })
    flash('延伸報告產生中，稍候頁面會自動更新。', 'info')
    return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=aid))


@bp.route('/<pid>/analyses/<aid>/derive/status')
@project_access_required(min_role='viewer')
def derive_status(pid, aid, project, role):
    """輪詢延伸報告產生進度（JSON）。完成時把各份存回 analyses doc。"""
    doc = _analysis_ref(pid, aid).get()
    if not doc.exists:
        return jsonify({'status': 'error', 'error': '找不到分析'}), 404
    return jsonify(_reconcile_derive(pid, aid, doc.to_dict()))


@bp.route('/<pid>/analyses/<aid>/derived/<kind>')
@project_access_required(min_role='viewer')
def view_derived_report(pid, aid, project, role, kind):
    """檢視單份延伸報告（Markdown 渲染）。kind＝專家 slug（動態）。"""
    if not kb_store.slug_ok(kind):
        abort(404)
    doc = _analysis_ref(pid, aid).get()
    if not doc.exists:
        abort(404)
    analysis = doc.to_dict()
    md = (analysis.get('derived_reports') or {}).get(kind)
    if not md:
        flash('此延伸報告尚未產生。', 'warning')
        return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=aid))
    return render_template('derived_report.html', pid=pid, aid=aid, project=project,
                           kind=kind, kind_label=_derived_label(analysis, kind),
                           report_title=analysis.get('report_title', ''),
                           markdown=md)


@bp.route('/<pid>/analyses/<aid>/derived/<kind>.md')
@project_access_required(min_role='viewer')
def download_derived_report(pid, aid, project, role, kind):
    """下載單份延伸報告（.md）。kind＝專家 slug（動態）。"""
    if not kb_store.slug_ok(kind):
        abort(404)
    doc = _analysis_ref(pid, aid).get()
    if not doc.exists:
        abort(404)
    analysis = doc.to_dict()
    md = (analysis.get('derived_reports') or {}).get(kind)
    if not md:
        flash('此延伸報告尚未產生。', 'warning')
        return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=aid))
    raw_title = analysis.get('report_title', 'report')
    base = re.sub(r'[^\w\-. ]', '_', raw_title).strip()[:60]
    stream = BytesIO(md.encode('utf-8'))
    return send_file(stream, as_attachment=True,
                     download_name=f"{base}_{kind}.md",
                     mimetype='text/markdown; charset=utf-8')


@bp.route('/<pid>/analyses/<aid>/rename', methods=['POST'])
@project_access_required(min_role='editor')
def rename_analysis(pid, aid, project, role):
    """更名分析報告（report_title）。"""
    new_title = request.form.get('report_title', '').strip()[:200]
    if not new_title:
        flash('請填寫新的報告標題。', 'danger')
        return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=aid))
    ref = (db.collection('projects').document(pid)
           .collection('analyses').document(aid))
    if not ref.get().exists:
        abort(404)
    ref.update({'report_title': new_title,
                'updated_at': firestore.SERVER_TIMESTAMP})
    flash('報告標題已更新。', 'success')
    return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=aid))


@bp.route('/<pid>/analyses/<aid>/delete', methods=['POST'])
@project_access_required(min_role='editor')
def delete_analysis(pid, aid, project, role):
    """刪除分析報告；若仍在進行中則先請求分析引擎強制停止（廢除執行階段），再移除記錄。"""
    ref = (db.collection('projects').document(pid)
           .collection('analyses').document(aid))
    doc = ref.get()
    if not doc.exists:
        abort(404)
    analysis = doc.to_dict()
    status = analysis.get('status')
    job_id = analysis.get('job_id')

    stopped = False
    if status in ('pending', 'running') and job_id:
        res = cancel_analysis(job_id)
        stopped = 'error' not in res
        log_usage('stop_analysis', detail=analysis.get('report_title', ''),
                  project_id=pid)

    ref.delete()
    log_usage('delete_analysis', detail=analysis.get('report_title', ''),
              project_id=pid)
    if stopped:
        flash('已強制停止分析、廢除執行階段並刪除報告。', 'success')
    else:
        flash('分析報告已刪除。', 'success')
    return redirect(url_for('project_bp.project_detail', pid=pid))


# ──────────────────────────────────────────────────────────────────────
# 資料集（爬取文件）：輸入 URL → 後端非同步爬取 → 文件 → 一鍵分析
# Firestore: projects/{pid}/datasets/{did}
# ──────────────────────────────────────────────────────────────────────

@bp.route('/<pid>/analyses/combine', methods=['POST'])
@project_access_required(min_role='editor')
def combine_analyses(pid, project, role):
    """🧩 整合報告（階段③）：選 1 筆文字報告 + 1 筆視覺報告 → 整合策略報告。
    產物本身也存成 analyses（kind='combined'），列入歷史分析。"""
    ids = request.form.getlist('analysis_ids')
    if len(ids) < 2:
        flash('請勾選一份文字報告與一份視覺報告（共兩筆）。', 'warning')
        return redirect(url_for('project_bp.project_detail', pid=pid))
    docs = []
    for aid in ids:
        d = (db.collection('projects').document(pid)
             .collection('analyses').document(aid).get())
        if d.exists:
            docs.append(d.to_dict())
    text_doc = next((v for v in docs if v.get('kind') not in ('visual', 'combined')), None)
    visual_doc = next((v for v in docs if v.get('kind') == 'visual'), None)
    if not text_doc or not visual_doc:
        flash('整合需要「一份文字報告」+「一份視覺報告」，請確認勾選。', 'warning')
        return redirect(url_for('project_bp.project_detail', pid=pid))
    if text_doc.get('status') != 'completed' or visual_doc.get('status') != 'completed':
        flash('兩份報告都需「已完成」才能整合。', 'warning')
        return redirect(url_for('project_bp.project_detail', pid=pid))
    text_md = text_doc.get('result_markdown') or ''
    visual_md = visual_doc.get('result_markdown') or ''
    if not text_md or not visual_md:
        flash('報告內容尚未就緒，請稍候再試。', 'warning')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    cfg = project.get('llm_config', {}) or {}
    provider = (cfg.get('provider') or 'gemini').lower()
    model = cfg.get('model') or 'gemini-2.5-flash'
    api_key = cfg.get('api_key') or ''
    if not api_key:
        flash('請先在專案設定填入 LLM API Key。', 'warning')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    topic = (text_doc.get('report_title') or '').strip() or '整合主題'
    title = f"{topic}（整合報告）"
    result = submit_combined(title, text_md, visual_md, provider, model, api_key,
                             topic=topic, temperature=cfg.get('temperature', 0.3))
    if 'error' in result:
        flash(f'啟動整合失敗：{result["error"]}', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    job_id = result.get('job_id')
    ref = (db.collection('projects').document(pid).collection('analyses').document())
    ref.set({
        'id': ref.id, 'job_id': job_id, 'kind': 'combined',
        'report_title': title, 'status': 'pending', 'progress': 0,
        'log': '整合報告已提交，等待處理...',
        'llm_provider': provider, 'llm_model': model,
        'submitted_by': current_user_email(),
        'submitted_at': firestore.SERVER_TIMESTAMP,
        'completed_at': None, 'result_markdown': None,
        'source_text': text_doc.get('id'), 'source_visual': visual_doc.get('id'),
    })
    log_usage('combine', detail=title, count=2, project_id=pid)
    flash('已提交整合報告（文字 × 視覺）。完成後列入歷史分析。', 'success')
    return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=ref.id))


@bp.route('/<pid>/analyses/combined', methods=['POST'])
@project_access_required(min_role='editor')
def analyse_combined(pid, project, role):
    """合併多個資料集的成功項目，提交一份分析。

    例：爬蟲時尚媒體資料集 + Cowork 蒐集的 Dcard 資料集合併分析。
    form: dataset_ids（多選）+ report_title。
    """
    dataset_ids = request.form.getlist('dataset_ids')
    if not dataset_ids:
        flash('請至少勾選一個資料集。', 'warning')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    contents = []
    used_names = []
    for did in dataset_ids:
        doc = (db.collection('projects').document(pid)
               .collection('datasets').document(did).get())
        if not doc.exists:
            continue
        ds = doc.to_dict()
        if ds.get('status') != 'completed':
            continue
        used_names.append(ds.get('name', did))
        for it in _load_dataset_items(pid, did):
            if it.get('status') == 'success' and it.get('content'):
                contents.append({
                    'url': it.get('url', ''),
                    'title': it.get('title', ''),
                    'text': it.get('content', ''),
                    'source_type': 'media',
                })
    if not contents:
        flash('勾選的資料集中沒有可分析的成功項目（或尚未完成）。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))
    if len(contents) > 100:
        flash(f'合併後共 {len(contents)} 篇，超過單次分析上限 100 篇，請減少資料集。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    llm_config = project.get('llm_config', {})
    if not llm_config.get('api_key'):
        flash('尚未設定 LLM API Key，請先至專案設定填入。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    report_title = (request.form.get('report_title', '').strip()
                    or f'合併分析：{"、".join(used_names)[:50]}')

    result = submit_analysis(
        report_title=report_title,
        contents=contents,
        llm_provider=llm_config.get('provider', 'gemini'),
        llm_model=llm_config.get('model', 'gemini-2.5-flash'),
        llm_api_key=llm_config.get('api_key'),
        temperature=llm_config.get('temperature', 0.3),
        thinking=llm_config.get('thinking', False),
        search_extent=llm_config.get('search_extent', True),
        max_output_tokens=llm_config.get('max_output_tokens', 8192),
        top_p=llm_config.get('top_p'),
        input_scale=llm_config.get('input_scale', 'standard'),
    )
    if 'error' in result:
        flash(f'提交分析失敗：{result["error"]}', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    job_id = result.get('job_id')
    analysis_ref = (db.collection('projects').document(pid)
                    .collection('analyses').document())
    analysis_ref.set({
        'id': analysis_ref.id,
        'job_id': job_id,
        'report_title': report_title,
        'status': 'pending',
        'progress': 0,
        'log': '任務已提交，等待分析引擎處理...',
        'n_articles': len(contents),
        'llm_provider': llm_config.get('provider', 'gemini'),
        'llm_model': llm_config.get('model', 'gemini-2.5-flash'),
        'submitted_by': current_user_email(),
        'submitted_at': firestore.SERVER_TIMESTAMP,
        'completed_at': None,
        'result_markdown': None,
        'source_dataset': ','.join(dataset_ids),
    })
    log_usage('analyse', detail=report_title, count=len(contents), project_id=pid)
    flash(f'已合併 {len(used_names)} 個資料集提交分析（{len(contents)} 篇）。', 'success')
    return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=analysis_ref.id))


# ──────────────────────────────────────────────────────────────────────
# 資料集下載（原始爬取內文）：Markdown / JSON
# ──────────────────────────────────────────────────────────────────────

# 資料集匯出層已抽出（見 dataset_export.py）。
from ..dataset_export import _dataset_to_markdown, _dataset_to_json  # noqa: F401
