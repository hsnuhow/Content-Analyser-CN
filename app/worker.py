import os
import time
import traceback
import threading
from flask import current_app
from firebase_admin import firestore
from .crawler import HeadlessCrawler
from .services import db, get_secret

# [Feature] Global Lock for Concurrency Control
CRAWLER_LOCK = threading.Lock()
CURRENT_CRAWLER_INSTANCE = None

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
    """The main pipeline for content analysis (Serialized Version)."""
    global CURRENT_CRAWLER_INSTANCE
    
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
            except: pass
            return False

        # Helper for crawler logs
        def crawler_log_handler(message):
            if _check_cancellation(): return
            try:
                project_ref.update({
                    'log': message,
                    'updated_at': firestore.SERVER_TIMESTAMP
                })
                print(f"Project {project_id} [Crawler]: {message}")
            except Exception as e:
                print(f"[Worker] Log update failed: {e}")

        # [Feature] Acquire Global Lock
        _update(1, "Waiting for queue position... (Only 1 task allowed at a time)")
        
        if not CRAWLER_LOCK.acquire(timeout=300): # Wait up to 5 mins
            _update(0, "Task timed out waiting for queue.")
            project_ref.update({'status': 'failed'})
            return

        try:
            # Got lock!
            _update(5, "Initializing crawler...")
            
            # API Key Logic
            api_key = get_user_gemini_key(user_email)
            if api_key:
                _update(7, "Using User's Gemini API Key")
            else:
                api_key = os.environ.get('GENAI_API_KEY')
                if api_key:
                    _update(7, "Using System Default Gemini API Key")
                else:
                    _update(7, "Warning: No Gemini API Key found.")

            if _check_cancellation(): return

            # Initialize Crawler
            crawler = HeadlessCrawler(log_callback=crawler_log_handler)
            CURRENT_CRAWLER_INSTANCE = crawler # Register instance for force kill
            
            if api_key:
                crawler.configure_genai(api_key)

            urls = data.get('urls', [])
            report_title = data.get('report_title', 'Untitled Project')
            
            total_urls = len(urls)
            _update(15, f"Starting to process {total_urls} URLs...")

            for i, url in enumerate(urls):
                if _check_cancellation():
                    _update(0, "Task cancelled by user.")
                    return

                url = url.strip()
                if not url: continue
                
                step_progress = 15 + int((i / total_urls) * 80)
                _update(step_progress, f"Processing ({i+1}/{total_urls}): {url}")
                
                result = crawler.scrape(url)
                
                # Persist Page Data
                page_data = {
                    'url': url,
                    'status': result['status'],
                    'crawled_at': firestore.SERVER_TIMESTAMP
                }
                
                if result['status'] == 'success':
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
            except: pass
        finally:
            if crawler:
                crawler.close()
            CURRENT_CRAWLER_INSTANCE = None # Deregister
            CRAWLER_LOCK.release() # Release lock
