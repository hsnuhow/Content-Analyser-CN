# -*- coding: utf-8 -*-
"""資料集領域路由：建立/爬取/手動匯入/詳情/狀態/分析/重爬/研究/大圖擷取/重新命名/刪除/下載。
註冊在 project_bp（package __init__ 於底部 import 本模組以套用 @bp.route）。"""
import json
import re
from io import BytesIO
from flask import (request, jsonify, flash, redirect, url_for, send_file, abort,
                   render_template)
from firebase_admin import firestore

from . import bp, project_access_required, current_user_email, log_usage
from ..services import db
from ..crawler_client import (submit_crawl_batch, cancel_crawl,
                              submit_research, get_research_status,
                              submit_extract_images, get_extract_images_status)
from ..analysis_client import submit_analysis, submit_image_analysis
from ..url_utils import parse_url_list, _url_key
from ..doc_extract import _extract_doc_text
from ..datasets_store import (_items_ref, _load_dataset_items, _save_dataset_items,
                              _delete_dataset_items)
from ..dataset_sync import _sync_crawling_dataset
from ..dataset_export import _dataset_to_markdown, _dataset_to_json

def _get_completed_dataset_or_redirect(pid: str, did: str):
    """讀取已完成的資料集；未完成回 (None, redirect_response)。"""
    doc = (db.collection('projects').document(pid)
           .collection('datasets').document(did).get())
    if not doc.exists:
        abort(404)
    dataset = doc.to_dict()
    if dataset.get('status') != 'completed':
        flash('資料集尚未爬取完成，無法下載。', 'warning')
        return None, redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))
    dataset['items'] = _load_dataset_items(pid, did)  # items 改存子集合
    return dataset, None


@bp.route('/<pid>/datasets/<did>/download.md')
@project_access_required(min_role='viewer')
def download_dataset_md(pid, did, project, role):
    """下載資料集原始爬取內文（Markdown）。"""
    dataset, resp = _get_completed_dataset_or_redirect(pid, did)
    if resp:
        return resp
    md = _dataset_to_markdown(dataset)
    fname = re.sub(r'[^\w\-. ]', '_', (dataset.get('name') or 'dataset')).strip()[:80]
    return send_file(BytesIO(md.encode('utf-8')), as_attachment=True,
                     download_name=f"{fname}.md",
                     mimetype='text/markdown; charset=utf-8')


@bp.route('/<pid>/datasets/<did>/download.json')
@project_access_required(min_role='viewer')
def download_dataset_json(pid, did, project, role):
    """下載資料集原始爬取內文（結構化 JSON，含全部項目）。"""
    dataset, resp = _get_completed_dataset_or_redirect(pid, did)
    if resp:
        return resp
    payload = json.dumps(_dataset_to_json(dataset), ensure_ascii=False, indent=2)
    fname = re.sub(r'[^\w\-. ]', '_', (dataset.get('name') or 'dataset')).strip()[:80]
    return send_file(BytesIO(payload.encode('utf-8')), as_attachment=True,
                     download_name=f"{fname}.json",
                     mimetype='application/json; charset=utf-8')


# ──────────────────────────────────────────────────────────────────────
# 資料集更名 / 刪除（含強制停止爬取）
# ──────────────────────────────────────────────────────────────────────

@bp.route('/<pid>/datasets/<did>/rename', methods=['POST'])
@project_access_required(min_role='editor')
def rename_dataset(pid, did, project, role):
    """更名資料集。"""
    new_name = request.form.get('name', '').strip()[:200]
    if not new_name:
        flash('請填寫新的資料集名稱。', 'danger')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))
    ref = (db.collection('projects').document(pid)
           .collection('datasets').document(did))
    if not ref.get().exists:
        abort(404)
    ref.update({'name': new_name, 'updated_at': firestore.SERVER_TIMESTAMP})
    flash('資料集名稱已更新。', 'success')
    return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))


@bp.route('/<pid>/datasets/<did>/items/<item_id>/delete', methods=['POST'])
@project_access_required(min_role='editor')
def delete_dataset_item(pid, did, item_id, project, role):
    """從資料集刪除單一網址項目（待爬 URL 或已爬內容皆適用），並重算計數。"""
    item_ref = _items_ref(pid, did).document(item_id)
    if not item_ref.get().exists:
        flash('找不到該項目（可能已被刪除）。', 'warning')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))
    item_ref.delete()
    # 重算 item_count / succeeded（以剩餘 items 為準）
    remaining = [d.to_dict() for d in _items_ref(pid, did).stream()]
    db.collection('projects').document(pid).collection('datasets').document(did).update({
        'item_count': len(remaining),
        'succeeded': sum(1 for it in remaining if it.get('status') == 'success'),
        'updated_at': firestore.SERVER_TIMESTAMP,
    })
    flash('已刪除該網址項目。', 'success')
    return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))


@bp.route('/<pid>/datasets/<did>/delete', methods=['POST'])
@project_access_required(min_role='editor')
def delete_dataset(pid, did, project, role):
    """刪除資料集；若仍在爬取中則先請求爬蟲強制停止（廢除執行階段），再移除記錄。"""
    ref = (db.collection('projects').document(pid)
           .collection('datasets').document(did))
    doc = ref.get()
    if not doc.exists:
        abort(404)
    dataset = doc.to_dict()
    status = dataset.get('status')
    crawl_job_id = dataset.get('crawl_job_id')

    stopped = False
    if status == 'crawling' and crawl_job_id:
        res = cancel_crawl(crawl_job_id)
        stopped = 'error' not in res
        log_usage('stop_crawl', detail=dataset.get('name', ''), project_id=pid)

    _delete_dataset_items(pid, did)  # 先刪 items 子集合
    ref.delete()
    log_usage('delete_dataset', detail=dataset.get('name', ''), project_id=pid)
    if stopped:
        flash('已強制停止爬取、廢除執行階段並刪除資料集。', 'success')
    else:
        flash('資料集已刪除。', 'success')
    return redirect(url_for('project_bp.project_detail', pid=pid))




@bp.route('/<pid>/datasets', methods=['POST'])
@project_access_required(min_role='editor')
def create_dataset(pid, project, role):
    """貼上 URL → 建立『草稿』資料集（只保存清單，不送爬蟲）。
    清單持久化、可逐筆刪除、重載不消失；按「開始爬取」才正式送入爬蟲（start_crawl）。
    此階段只是保存使用者輸入的記憶，與後續所有爬取流程解耦。"""
    name = request.form.get('name', '').strip()
    use_gemini = bool(request.form.get('use_gemini'))

    urls = parse_url_list(request.form.get('urls', ''))   # 已正規化去重（A）
    if not name:
        flash('請填寫資料集名稱。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))
    if not urls:
        flash('請至少輸入一個網址。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))
    if len(urls) > 1000:
        flash('單次最多 1000 個網址（如需更多請分次或用重爬續加）。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    ds_ref = db.collection('projects').document(pid).collection('datasets').document()
    ds_ref.set({
        'id': ds_ref.id,
        'name': name,
        'source_urls': urls,
        'crawl_job_id': None,
        'status': 'draft',           # 草稿：只存清單，尚未爬取
        'use_gemini': use_gemini,    # 開始爬取時沿用此偏好
        'progress': 0,
        'log': '草稿清單已建立，確認後按「開始爬取」。',
        'item_count': len(urls),
        'created_by': current_user_email(),
        'created_at': firestore.SERVER_TIMESTAMP,
        'updated_at': firestore.SERVER_TIMESTAMP,
    })
    # 待爬 items（status='pending'）：可逐筆刪除、重載不消失。
    _save_dataset_items(pid, ds_ref.id, [{'url': u, 'status': 'pending'} for u in urls])
    flash(f'草稿資料集「{name}」已建立（{len(urls)} 個網址）。確認後按「開始爬取」。', 'success')
    return redirect(url_for('project_bp.dataset_detail', pid=pid, did=ds_ref.id))


@bp.route('/<pid>/datasets/<did>/crawl', methods=['POST'])
@project_access_required(min_role='editor')
def start_crawl(pid, did, project, role):
    """把草稿清單目前的網址送入爬蟲開始爬取（draft → crawling）。
    以「目前 items 清單」為準（使用者可能已刪除部分）；失敗保留草稿可重試。"""
    ref = (db.collection('projects').document(pid)
           .collection('datasets').document(did))
    doc = ref.get()
    if not doc.exists:
        abort(404)
    dataset = doc.to_dict()
    if dataset.get('status') != 'draft':
        flash('此資料集不是草稿狀態，無法開始爬取（已爬過的請用「重新爬取」）。', 'warning')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))
    items = _load_dataset_items(pid, did)
    urls = list(dict.fromkeys(it.get('url') for it in items if it.get('url')))
    if not urls:
        flash('資料集沒有可爬取的網址。', 'danger')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))

    llm_config = project.get('llm_config', {})
    gemini_key = llm_config.get('api_key') if llm_config.get('provider') == 'gemini' else None
    result = submit_crawl_batch(urls, use_gemini=bool(dataset.get('use_gemini')),
                                gemini_api_key=gemini_key)
    if 'error' in result:
        flash(f'啟動爬取失敗：{result["error"]}（草稿資料集已保留，可稍後重試）', 'danger')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))

    ref.update({
        'crawl_job_id': result.get('job_id'),
        'status': 'crawling',
        'progress': 0,
        'log': '已提交爬取任務...',
        'source_urls': urls,
        'item_count': len(urls),
        'updated_at': firestore.SERVER_TIMESTAMP,
    })
    log_usage('crawl', detail=dataset.get('name', ''), count=len(urls), project_id=pid)
    flash(f'已開始爬取 {len(urls)} 個網址...', 'success')
    return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))


# 上傳檔文字抽取（_extract_doc_text）實作見 doc_extract.py（已於檔案頂部 import）。
@bp.route('/<pid>/datasets/manual', methods=['POST'])
@project_access_required(min_role='editor')
def create_manual_dataset(pid, project, role):
    """手動/上傳建立資料集（不經爬蟲）：供 Claude Cowork 等外部蒐集的內容匯入。

    輸入：name + items_json（貼上）或上傳檔 file（皆為 JSON 陣列）。
    每筆格式：{"url","title","text"}（text 亦相容 content）。
    直接建立 status=completed 的資料集，items 與爬蟲結果同 schema，可照常一鍵分析。
    """
    name = request.form.get('name', '').strip()
    if not name:
        flash('請填寫資料集名稱。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    _MAX_FILE = 3 * 1024 * 1024          # 單檔 3MB
    items = []
    seen_keys = set()                    # 資料集內 URL 去重
    skipped_dup = 0

    def _add(title, text, url=''):
        nonlocal skipped_dup
        text = (text or '').strip()
        if not text:
            return
        key = _url_key(url)
        if key:
            if key in seen_keys:
                skipped_dup += 1
                return
            seen_keys.add(key)
        text = text[:50000]
        items.append({'url': url, 'title': (title or '未命名').strip()[:200] or '未命名',
                      'content': text, 'length': len(text),
                      'status': 'success', 'source': 'manual'})

    docs = [f for f in request.files.getlist('docs') if f and f.filename]
    paste_text = request.form.get('paste_text', '').strip()
    raw_json = request.form.get('items_json', '').strip()
    json_file = request.files.get('file')

    if docs:
        # 模式一：上傳檔案（txt/md/docx），每檔一筆（標題＝檔名）
        for f in docs:
            blob = f.read(_MAX_FILE + 1)
            if len(blob) > _MAX_FILE:
                flash(f'檔案「{f.filename}」過大（單檔上限 3MB）。', 'danger')
                return redirect(url_for('project_bp.project_detail', pid=pid))
            txt, err = _extract_doc_text(f.filename, blob)
            if err:
                flash(err, 'danger')
                return redirect(url_for('project_bp.project_detail', pid=pid))
            base = f.filename.rsplit('.', 1)[0]
            _add(base, txt, url='')
    elif paste_text:
        # 模式二：貼上文字 → 一筆（標題用「貼上標題」或資料集名）
        _add(request.form.get('paste_title', '').strip() or name, paste_text, url='')
    else:
        # 模式三：進階 JSON（貼上 items_json 或上傳 JSON 檔）
        raw = raw_json
        if json_file and json_file.filename:
            blob = json_file.read(12 * 1024 * 1024 + 1)
            if len(blob) > 12 * 1024 * 1024:
                flash('上傳 JSON 檔過大（上限 12MB）。', 'danger')
                return redirect(url_for('project_bp.project_detail', pid=pid))
            raw = blob.decode('utf-8', 'ignore').strip()
        if not raw:
            flash('請選擇：上傳檔案、貼上文字、或進階 JSON。', 'danger')
            return redirect(url_for('project_bp.project_detail', pid=pid))
        if len(raw) > 12 * 1024 * 1024:
            flash('貼上內容過大（上限約 12MB），請拆分後再匯入。', 'danger')
            return redirect(url_for('project_bp.project_detail', pid=pid))
        try:
            data = json.loads(raw)
            if not isinstance(data, list) or not data:
                raise ValueError('內容必須是非空 JSON 陣列')
            if len(data) > 1000:
                raise ValueError('每個資料集最多 1000 筆')
        except Exception as e:
            flash(f'JSON 解析失敗：{e}', 'danger')
            return redirect(url_for('project_bp.project_detail', pid=pid))
        for i, it in enumerate(data):
            if not isinstance(it, dict):
                flash(f'第 {i+1} 筆不是物件。', 'danger')
                return redirect(url_for('project_bp.project_detail', pid=pid))
            _add(str(it.get('title') or '').strip() or f'項目 {i+1}',
                 str(it.get('text') or it.get('content') or ''),
                 str(it.get('url') or '').strip())

    if not items:
        flash('沒有可用內容（檔案／文字為空，或每筆 JSON 需有 text）。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))
    if len(items) > 1000:
        flash('每個資料集最多 1000 筆，請拆分後再匯入。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    # items 改存子集合（無 1MB 上限），dataset 文件只放 metadata。
    succeeded = len(items)
    ds_ref = db.collection('projects').document(pid).collection('datasets').document()
    ds_ref.set({
        'id': ds_ref.id,
        'name': name,
        'source_urls': [it['url'] for it in items if it['url']],
        'crawl_job_id': None,
        'status': 'completed',
        'progress': 100,
        'log': f'手動匯入 {succeeded} 筆',
        'item_count': succeeded,
        'succeeded': succeeded,
        'failed': 0,
        'origin': 'manual',
        'created_by': current_user_email(),
        'created_at': firestore.SERVER_TIMESTAMP,
        'updated_at': firestore.SERVER_TIMESTAMP,
    })
    _save_dataset_items(pid, ds_ref.id, items)
    log_usage('manual_import', detail=name, count=succeeded, project_id=pid)
    _dup_note = f'（已去重跳過 {skipped_dup} 筆重複網址）' if skipped_dup else ''
    flash(f'資料集「{name}」已匯入 {succeeded} 筆，可直接分析。{_dup_note}', 'success')
    return redirect(url_for('project_bp.dataset_detail', pid=pid, did=ds_ref.id))


# ──────────────────────────────────────────────────────────────────────
# 資料集 items 子集合（projects/{pid}/datasets/{did}/items）
# 內文存子集合而非內嵌於 dataset 文件 → 無單文件 1MB 上限、筆數不受限。
# 每筆用 auto-id 文件 + `_seq` 單調遞增欄位（刪除後 append 不撞 id），讀取依 `_seq` 排序。
# ──────────────────────────────────────────────────────────────────────

# 爬取狀態同步（_sync_crawling_dataset）實作見 dataset_sync.py（已於檔案頂部 import）。
@bp.route('/<pid>/datasets/<did>')
@project_access_required(min_role='viewer')
def dataset_detail(pid, did, project, role):
    # 進入詳情頁主動同步：離開頁面後 crawler 跑完的結果在此回收。
    dataset = _sync_crawling_dataset(pid, did, allow_spawn=(role != 'viewer'))
    if dataset is None:
        abort(404)
    dataset['id'] = did
    dataset['items'] = _load_dataset_items(pid, did)  # items 改存子集合
    return render_template('dataset_detail.html',
                           project=project, pid=pid, dataset=dataset, role=role)


@bp.route('/<pid>/datasets/<did>/status')
@project_access_required(min_role='viewer')
def dataset_status(pid, did, project, role):
    """前端輪詢進度；同步邏輯共用 _sync_crawling_dataset。"""
    dataset = _sync_crawling_dataset(pid, did, allow_spawn=(role != 'viewer'))
    if dataset is None:
        return jsonify({'error': '找不到此資料集'}), 404
    return jsonify({'status': dataset.get('status', 'crawling'),
                    'progress': dataset.get('progress', 0),
                    'log': dataset.get('log', '')})


@bp.route('/<pid>/datasets/<did>/analyse', methods=['POST'])
@project_access_required(min_role='editor')
def analyse_dataset(pid, did, project, role):
    """一鍵：把資料集的成功項目送往 analysis-pipeline。"""
    ds_doc = (db.collection('projects').document(pid)
              .collection('datasets').document(did).get())
    if not ds_doc.exists:
        abort(404)
    dataset = ds_doc.to_dict()

    if dataset.get('status') != 'completed':
        flash('資料集尚未爬取完成，無法分析。', 'warning')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))

    # 取成功項目，轉成 analysis 的 contents（爬蟲回傳 content 欄位）
    items = _load_dataset_items(pid, did)
    contents = [
        {
            'url': it.get('url', ''),
            'title': it.get('title', ''),
            'text': it.get('content', ''),       # analysis 相容 content，但統一帶 text
            'source_type': 'media',
        }
        for it in items if it.get('status') == 'success' and it.get('content')
    ]
    if not contents:
        flash('資料集中沒有可分析的成功項目。', 'danger')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))

    # 與 submit_analysis_route 一致的保護：單次最多 100 篇、每篇截斷 50,000 字元
    # （資料集可達 1000 筆，未設限會把超量 payload 丟給 analysis-pipeline → 下游失敗/成本爆）。
    if len(contents) > 100:
        flash(f'成功項目 {len(contents)} 篇超過單次分析上限 100 篇，僅取前 100 篇。', 'warning')
        contents = contents[:100]
    truncated_count = 0
    for it in contents:
        if len(it['text']) > 50000:
            it['text'] = it['text'][:50000]
            truncated_count += 1
    if truncated_count:
        flash(f'注意：{truncated_count} 篇超過 50,000 字元，已截斷後送出。', 'warning')

    llm_config = project.get('llm_config', {})
    if not llm_config.get('api_key'):
        flash('尚未設定 LLM API Key，請先至專案設定填入。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    report_title = request.form.get('report_title', '').strip() or dataset.get('name', '分析報告')

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
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))

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
        'source_dataset': did,
    })
    log_usage('analyse', detail=report_title, count=len(contents), project_id=pid)
    flash(f'已從資料集「{dataset.get("name")}」提交分析（{len(contents)} 篇）。', 'success')
    return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=analysis_ref.id))


@bp.route('/<pid>/datasets/<did>/recrawl', methods=['POST'])
@project_access_required(min_role='editor')
def recrawl_dataset(pid, did, project, role):
    """重啟/續爬一個資料集：
      mode=failed（預設）：只重爬未成功（失敗/未爬）的項，保留已成功項並合併。
      mode=all：整份重爬。
    解決批次被時限切掉、或部分站台失敗後不必整批重來。
    """
    ds_doc = (db.collection('projects').document(pid)
              .collection('datasets').document(did).get())
    if not ds_doc.exists:
        abort(404)
    dataset = ds_doc.to_dict()
    if dataset.get('status') == 'crawling':
        flash('資料集正在爬取中，請先等待完成或強制停止。', 'warning')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))

    mode = request.form.get('mode', 'failed')
    force_listing = bool(request.form.get('force_listing'))  # 強制爬取被略過的列表/商品頁
    items = _load_dataset_items(pid, did)
    all_urls = dataset.get('source_urls') or [it.get('url') for it in items if it.get('url')]
    # 視為「已成功不需重爬」需：status=success + 有內容 + 字數達標（內容偏少 <500 字者納入重爬目標）
    success_urls = {it.get('url') for it in items
                    if it.get('status') == 'success' and it.get('content')
                    and (it.get('length') or len(it.get('content') or '')) >= 500}
    if mode == 'all':
        target_urls = [u for u in all_urls if u]
    else:
        target_urls = [u for u in all_urls if u and u not in success_urls]
    target_urls = list(dict.fromkeys(target_urls))  # 去重保序
    if not target_urls:
        flash('沒有需要重爬的項目（全部已成功）。', 'info')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))

    llm_config = project.get('llm_config', {})
    gemini_key = llm_config.get('api_key') if llm_config.get('provider') == 'gemini' else None
    result = submit_crawl_batch(target_urls, use_gemini=bool(gemini_key),
                                gemini_api_key=gemini_key, force_listing=force_listing)
    if 'error' in result:
        flash(f'啟動重爬失敗：{result["error"]}', 'danger')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))

    update = {
        'crawl_job_id': result.get('job_id'),
        'status': 'crawling',
        'progress': 0,
        'log': f'重新爬取 {len(target_urls)} 項（{mode}）...',
        'updated_at': firestore.SERVER_TIMESTAMP,
        # 啟動重爬即清掉舊的 SSRF 封鎖橫幅（被擋 URL 已納入本次重爬）；
        # 完成時 _sync_crawling_dataset 會依新 job 結果重寫，若仍被擋會誠實重現。
        'blocked': firestore.DELETE_FIELD,
        'n_blocked': firestore.DELETE_FIELD,
    }
    if mode == 'all':
        update['recrawl_urls'] = firestore.DELETE_FIELD  # 完成時整批替換
    else:
        update['recrawl_urls'] = target_urls  # 完成時只替換這些 url，保留已成功項
    (db.collection('projects').document(pid)
     .collection('datasets').document(did).update(update))
    log_usage('recrawl', detail=f"{dataset.get('name', '')}({mode})",
              count=len(target_urls), project_id=pid)
    flash(f'已啟動重爬 {len(target_urls)} 項，完成後{"整批替換" if mode == "all" else "合併保留已成功項"}。', 'success')
    return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))


@bp.route('/<pid>/datasets/<did>/research', methods=['POST'])
@project_access_required(min_role='editor')
def research_dataset(pid, did, project, role):
    """🔬 研究失敗項：對資料集中失敗的 URL 觸發選擇器研究 agent（on-demand）。
    產出候選選擇器（待 admin 確認升級）與失敗診斷。與爬蟲不並行。"""
    ds_doc = (db.collection('projects').document(pid)
              .collection('datasets').document(did).get())
    if not ds_doc.exists:
        abort(404)
    dataset = ds_doc.to_dict()
    if dataset.get('status') == 'crawling':
        flash('資料集正在爬取中，請先等爬完再研究。', 'warning')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))
    items = _load_dataset_items(pid, did)
    failed_urls = list(dict.fromkeys(
        it.get('url') for it in items
        if it.get('status') == 'failed' and it.get('url')))
    if not failed_urls:
        flash('沒有失敗的項目可研究。', 'info')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))

    result = submit_research(failed_urls)
    if 'error' in result:
        flash(f'啟動研究失敗：{result["error"]}', 'danger')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))

    (db.collection('projects').document(pid).collection('datasets').document(did)
     .update({'research_job_id': result.get('job_id'),
              'research_status': 'running',
              'updated_at': firestore.SERVER_TIMESTAMP}))
    log_usage('research', detail=dataset.get('name', ''),
              count=len(failed_urls), project_id=pid)
    flash(f'已啟動「失敗項研究」（{len(failed_urls)} 個 URL）。完成後此頁會顯示候選選擇器與診斷。', 'success')
    return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))


@bp.route('/<pid>/datasets/<did>/research/status')
@project_access_required(min_role='viewer')
def research_status(pid, did, project, role):
    """輪詢研究任務進度與結果（JSON）。"""
    ds_doc = (db.collection('projects').document(pid)
              .collection('datasets').document(did).get())
    if not ds_doc.exists:
        return jsonify({'error': '找不到資料集'}), 404
    job_id = (ds_doc.to_dict() or {}).get('research_job_id')
    if not job_id:
        return jsonify({'status': 'none'}), 200
    job = get_research_status(job_id)
    return jsonify({
        'status': job.get('status', 'unknown'),
        'log': job.get('log', ''),
        'result': job.get('result', {}),
    }), 200


@bp.route('/<pid>/datasets/<did>/research/clear', methods=['POST'])
@project_access_required(min_role='editor')
def research_clear(pid, did, project, role):
    """清除資料集頁上的「失敗項研究」結果面板（不影響已升級的 learned_selectors）。"""
    (db.collection('projects').document(pid).collection('datasets').document(did)
     .update({'research_job_id': firestore.DELETE_FIELD,
              'research_status': firestore.DELETE_FIELD,
              'updated_at': firestore.SERVER_TIMESTAMP}))
    flash('已清除研究結果面板。', 'success')
    return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))


@bp.route('/<pid>/datasets/<did>/extract-images', methods=['POST'])
@project_access_required(min_role='editor')
def extract_images_dataset(pid, did, project, role):
    """🖼 擷取主文大圖：對資料集成功項的 URL 觸發影像擷取（只取主文大圖、不碰文字）。
    與文字爬取嚴格分離（獨立 crawler 端點），on-demand。"""
    ds_doc = (db.collection('projects').document(pid)
              .collection('datasets').document(did).get())
    if not ds_doc.exists:
        abort(404)
    dataset = ds_doc.to_dict()
    if dataset.get('status') == 'crawling':
        flash('資料集正在爬取中，請先等爬完再擷取大圖。', 'warning')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))
    items = _load_dataset_items(pid, did)
    urls = list(dict.fromkeys(
        it.get('url') for it in items
        if it.get('status') == 'success' and it.get('url')))
    if not urls:
        flash('沒有成功的項目可擷取大圖。', 'info')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))

    result = submit_extract_images(urls)
    if 'error' in result:
        flash(f'啟動影像擷取失敗：{result["error"]}', 'danger')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))

    (db.collection('projects').document(pid).collection('datasets').document(did)
     .update({'image_job_id': result.get('job_id'),
              'image_status': 'running',
              'updated_at': firestore.SERVER_TIMESTAMP}))
    log_usage('extract_images', detail=dataset.get('name', ''),
              count=len(urls), project_id=pid)
    flash(f'已啟動「主文大圖擷取」（{len(urls)} 個成功項）。完成後此頁會顯示每篇抽到的大圖。', 'success')
    return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))


@bp.route('/<pid>/datasets/<did>/extract-images/status')
@project_access_required(min_role='viewer')
def extract_images_status(pid, did, project, role):
    """輪詢影像擷取任務進度與結果（JSON）。"""
    ds_doc = (db.collection('projects').document(pid)
              .collection('datasets').document(did).get())
    if not ds_doc.exists:
        return jsonify({'error': '找不到資料集'}), 404
    job_id = (ds_doc.to_dict() or {}).get('image_job_id')
    if not job_id:
        return jsonify({'status': 'none'}), 200
    job = get_extract_images_status(job_id)
    return jsonify({
        'status': job.get('status', 'unknown'),
        'log': job.get('log', ''),
        'n_images': job.get('n_images', 0),
        'results': job.get('results', []),
    }), 200


@bp.route('/<pid>/datasets/<did>/analyse-images', methods=['POST'])
@project_access_required(min_role='editor')
def analyse_images_dataset(pid, did, project, role):
    """🎨 大圖視覺分析（階段②）：把已擷取的主文大圖送 analysis-pipeline 做
    色調/色澤/主題/視覺吸睛要素分析，產出視覺報告。需先完成「擷取主文大圖」。"""
    ds_doc = (db.collection('projects').document(pid)
              .collection('datasets').document(did).get())
    if not ds_doc.exists:
        abort(404)
    dataset = ds_doc.to_dict()
    image_job_id = dataset.get('image_job_id')
    if not image_job_id:
        flash('請先「擷取主文大圖」，再進行視覺分析。', 'warning')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))
    job = get_extract_images_status(image_job_id)
    if job.get('status') != 'completed':
        flash('大圖擷取尚未完成，請稍候再分析。', 'warning')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))
    images = []
    for it in (job.get('results') or []):
        src_url = it.get('url')
        for im in (it.get('images') or []):
            if im.get('src'):
                images.append({'src': im['src'], 'alt': im.get('alt', ''),
                               'source_url': src_url})
    if not images:
        flash('沒有可分析的大圖。', 'info')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))

    cfg = project.get('llm_config', {}) or {}
    provider = (cfg.get('provider') or 'gemini').lower()
    model = cfg.get('model') or 'gemini-2.5-flash'
    api_key = cfg.get('api_key') or ''
    if not api_key:
        flash('請先在專案設定填入 LLM API Key。', 'warning')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))
    if provider not in ('gemini', 'claude'):
        flash('影像視覺分析僅支援 Gemini 或 Claude（建議 Gemini），請於專案設定切換。', 'warning')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))

    title = f"{dataset.get('name', '')}（視覺分析）"
    result = submit_image_analysis(title, images, provider, model, api_key,
                                   temperature=cfg.get('temperature', 0.3))
    if 'error' in result:
        flash(f'啟動視覺分析失敗：{result["error"]}', 'danger')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))

    # 與文字分析一致：建一筆正式 analyses doc（kind='visual'）→ 進「歷史分析」清單、
    # 有獨立報告頁、可下載/改名/刪除。result_markdown 由 analysis_status 輪詢時持久化。
    job_id = result.get('job_id')
    analysis_ref = (db.collection('projects').document(pid)
                    .collection('analyses').document())
    analysis_ref.set({
        'id': analysis_ref.id,
        'job_id': job_id,
        'kind': 'visual',
        'report_title': title,
        'status': 'pending',
        'progress': 0,
        'log': '影像視覺分析已提交，等待處理...',
        'n_images': len(images),
        'llm_provider': provider,
        'llm_model': model,
        'submitted_by': current_user_email(),
        'submitted_at': firestore.SERVER_TIMESTAMP,
        'completed_at': None,
        'result_markdown': None,
        'source_dataset': did,
    })
    log_usage('analyse_images', detail=title, count=len(images), project_id=pid)
    flash(f'已提交「大圖視覺分析」（{len(images)} 張）。完成後會列入歷史分析。', 'success')
    return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=analysis_ref.id))


