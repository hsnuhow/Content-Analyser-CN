import os
import time
import traceback
import threading
from flask import current_app
from firebase_admin import firestore
from .crawler_client import scrape_via_api
from .services import db, get_secret

# [Feature] Global Lock for Concurrency Control
# 獨立爬蟲服務為單 worker、重資源任務，主程式以全域鎖串行化呼叫，避免併發壓垮服務。
CRAWLER_LOCK = threading.Lock()

def get_user_gemini_key(user_email):
    try:
        if not user_email: return None
        doc = db.collection('users').document(user_email).get()
        if doc.exists:
            return doc.to_dict().get('gemini_api_key')
    except Exception as e:
        print(f"[Worker] Failed to fetch user key: {e}")
    return None

def analysis_pipeline(project_id, user_email, data, app):
    """The main pipeline for content analysis (Serialized Version).

    爬蟲已拆分為獨立 Cloud Run 服務，本管線透過 HTTP API (crawler_client) 呼叫。
    """
    with app.app_context():
        # Firestore Reference (Initialize early to update status even if blocked)
        project_ref = db.collection('users').document(user_email).collection('projects').document(project_id)

        # Helper to update progress
        def _update(prog, msg):
            try:
                project_ref.update({
                    'progress': prog,
                    'log': msg,
                    'updated_at': firestore.SERVER_TIMESTAMP
                })
                print(f"Project {project_id}: {msg} ({prog}%)")
            except Exception as e:
                print(f"[Worker] Firestore update failed: {e}")

        # Helper for cancellation check
        def _check_cancellation():
            try:
                doc = project_ref.get()
                if doc.exists and doc.to_dict().get('status') == 'cancelled':
                    return True
            except Exception:
                pass
            return False

        # [Feature] Acquire Global Lock
        _update(1, "Waiting for queue position... (Only 1 task allowed at a time)")

        if not CRAWLER_LOCK.acquire(timeout=300): # Wait up to 5 mins
            _update(0, "Task timed out waiting for queue.")
            project_ref.update({'status': 'failed'})
            return

        try:
            # Got lock!
            _update(5, "Initializing crawler service client...")

            # API Key Logic：優先使用者金鑰，其次系統預設
            api_key = get_user_gemini_key(user_email)
            if api_key:
                _update(7, "Using User's Gemini API Key")
            else:
                api_key = os.environ.get('GENAI_API_KEY')
                if api_key:
                    _update(7, "Using System Default Gemini API Key")
                else:
                    _update(7, "Warning: No Gemini API Key found.")

            if _check_cancellation():
                _update(0, "Task cancelled by user.")
                return

            urls = data.get('urls', [])
            use_gemini = bool(data.get('use_gemini', False))

            total_urls = len(urls)
            _update(15, f"Starting to process {total_urls} URLs...")

            for i, url in enumerate(urls):
                if _check_cancellation():
                    _update(0, "Task cancelled by user.")
                    return

                url = url.strip()
                if not url:
                    continue

                step_progress = 15 + int((i / total_urls) * 80)
                _update(step_progress, f"Processing ({i+1}/{total_urls}): {url}")

                # 透過 HTTP API 呼叫獨立爬蟲服務
                result = scrape_via_api(
                    url,
                    use_gemini=use_gemini,
                    gemini_api_key=api_key,
                )

                # Persist Page Data
                page_data = {
                    'url': url,
                    'status': result.get('status', 'failed'),
                    'crawled_at': firestore.SERVER_TIMESTAMP
                }

                if result.get('status') == 'success':
                    page_data['title'] = result.get('title')
                    page_data['content'] = result.get('content')
                    page_data['length'] = result.get('length')

                    title = result.get('title', 'No Title')
                    length = result.get('length', 0)
                    _update(step_progress + 5, f"✓ Success: {title[:20]}... ({length} chars)")
                else:
                    page_data['error'] = result.get('error')
                    error = result.get('error', 'Unknown Error')
                    _update(step_progress + 5, f"✗ Failed: {error}")

                project_ref.collection('pages').add(page_data)

            _update(100, "Analysis complete! Ready for download.")
            project_ref.update({'status': 'completed'})

        except Exception as e:
            print(f"Project {project_id} failed: {e}")
            traceback.print_exc()
            try:
                project_ref.update({
                    'status': 'failed',
                    'log': f"Critical Error: {e}"
                })
            except Exception:
                pass
        finally:
            CRAWLER_LOCK.release() # Release lock
