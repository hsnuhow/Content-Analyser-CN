import os
from datetime import timedelta
from flask import Flask, request
from authlib.integrations.flask_client import OAuth
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix
from . import services # Initialize Firebase

oauth = OAuth()
csrf = CSRFProtect()

def create_app():
    app = Flask(__name__)

    # Apply ProxyFix middleware
    # x_proto=1: Trust X-Forwarded-Proto (https/http)
    # x_host=1: Trust X-Forwarded-Host
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    is_dev = os.environ.get('FLASK_DEBUG') == '1'

    # Basic Config
    # 安全：正式環境必須由 Secret Manager 注入 SECRET_KEY。若缺失，絕不可退回固定值
    # （否則 session 以公開常數簽章 → 可偽造 approved/admin session、auth bypass）。
    _secret = os.environ.get('SECRET_KEY')
    if not _secret:
        if is_dev:
            _secret = 'dev_secret_key'
        else:
            raise RuntimeError(
                "SECRET_KEY 未設定（正式環境必須由 Secret Manager 注入）。拒絕以預設值啟動。")
    app.config['SECRET_KEY'] = _secret
    app.config['GOOGLE_CLIENT_ID'] = os.environ.get('GOOGLE_CLIENT_ID')
    app.config['GOOGLE_CLIENT_SECRET'] = os.environ.get('GOOGLE_CLIENT_SECRET')

    # 安全：限制請求 body 上限，擋「貼上超大 JSON / 上傳大檔」在解析階段吃滿 1Gi 記憶體 OOM。
    # （手動匯入 items_json 表單欄位原本無上限，Werkzeug 3.0.x 預設亦無 body 上限。）
    # 16MB 對合法貼上/上傳綽綽有餘（檔案另有 3MB 路由層上限、分析另有 100 篇/5M 字元上限）。
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

    # 安全：session 設有限壽命 + 每次請求續期。配合 auth_guards 的白名單 TTL 回查，
    # 縮短「admin 撤銷後既有 session 仍有效」的視窗（避免無到期時間的永久 cookie）。
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=12)

    # 來源鎖定（方案 B）：只接受經 Cloudflare（注入 X-Origin-Token 標頭）的請求，擋
    # 「直接打 *.run.app 公開網址」繞過 Cloudflare WAF。密鑰由 Secret Manager 注入
    # （ORIGIN_VERIFY_TOKEN），須與 Cloudflare Transform Rule 注入的值一致。
    # 分段上線：ENFORCE_ORIGIN_TOKEN=1 才阻擋(403)；其餘值（含未設）只記 log 不阻擋，
    # 避免 Cloudflare 尚未設好時把自己鎖在門外。未設密鑰（本地開發）→ 守衛停用。
    origin_token = os.environ.get('ORIGIN_VERIFY_TOKEN', '')
    enforce_origin = os.environ.get('ENFORCE_ORIGIN_TOKEN') == '1'

    # [Fix] Session Cookie Configuration for Preview/Dev
    # In production (Cloud Run), we want Secure cookies.
    # In development (Preview), we need to relax this to allow cookies over HTTP or complex proxies.

    if is_dev:
        app.config['SESSION_COOKIE_SECURE'] = False
        app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    else:
        app.config['SESSION_COOKIE_SECURE'] = True
        app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    
    # Token 成本估算：開放給模板用（est_cost(model, prompt, output) / est_embed_cost(chars)）
    from . import pricing
    app.jinja_env.globals['est_cost'] = pricing.est_cost_usd
    app.jinja_env.globals['est_embed_cost'] = pricing.est_embed_cost_usd

    # Initialize OAuth
    oauth.init_app(app)

    # Initialize CSRF protection：保護所有 POST 表單（session-based）。
    # 所有受保護表單已加入 {{ csrf_token() }} hidden input。
    # 注意：OAuth callback 為 GET（authlib 自有 state 防護），不受影響。
    csrf.init_app(app)
    
    # Register Google OAuth
    oauth.register(
        name='google',
        client_id=app.config['GOOGLE_CLIENT_ID'],
        client_secret=app.config['GOOGLE_CLIENT_SECRET'],
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'}
    )

    # 來源鎖定守衛（方案 B，見上方設定）：註冊在最前面，先於 session 與路由處理。
    # health 探活與 OAuth callback 不豁免——上線時 Cloudflare 對整個網域注入標頭即可；
    # Cloud Run 預設用 TCP 啟動探針（非 HTTP），不受影響。enforce 前會先看 log 驗證。
    @app.before_request
    def _verify_origin_token():
        if not origin_token:
            return  # 未設密鑰 → 守衛停用（本地開發 / 尚未導入）
        import hmac
        from flask import request, abort
        provided = request.headers.get('X-Origin-Token', '')
        if provided and hmac.compare_digest(provided, origin_token):
            return  # 合法來源（經 Cloudflare 注入標頭）
        if enforce_origin:
            abort(403)  # 直打 run.app 或缺/錯標頭 → 拒絕
        print(f"[OriginGuard] 缺/錯 X-Origin-Token（log-only，未阻擋）path={request.path}",
              flush=True)

    # 每次請求標記 session 為 permanent → 套用 PERMANENT_SESSION_LIFETIME 並滑動續期
    # （閒置逾 12 小時自動失效；配合白名單 TTL 回查使撤銷及時生效）。
    @app.before_request
    def _make_session_permanent():
        from flask import session
        session.permanent = True

    # 全域 context processor：所有 blueprint 的模板都能取得 user / is_admin
    # （原本註冊在 main_bp，導致 project_bp / admin_bp 頁面 navbar 顯示異常）
    @app.context_processor
    def inject_user():
        from flask import session
        from .services import get_admin_email
        user = session.get('user')
        is_admin = False
        if user:
            admin_email = get_admin_email()
            if admin_email:
                is_admin = user.get('email', '').lower() == admin_email.lower()
        return dict(user=user, is_admin=is_admin)

    # 安全標頭（縱深防禦）：防點擊劫持、MIME 嗅探；正式環境加 HSTS。
    # CSP 保留 'unsafe-inline'（模板含 inline script，移除會破壞 UI）；
    # 報告 HTML 的 XSS 主防線仍為前端 DOMPurify。frame-ancestors/X-Frame-Options 防 iframe 劫持。
    @app.after_request
    def _security_headers(resp):
        resp.headers.setdefault('X-Frame-Options', 'DENY')
        resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
        resp.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        # HTML 頁面禁快取（每次重新驗證）→ 部署後不再看到舊版 UI。
        # 只針對 HTML；靜態檔（/static）由 Flask 自有快取標頭處理，不覆蓋。
        ctype = (resp.headers.get('Content-Type') or '')
        if ctype.startswith('text/html') and not request.path.startswith('/static'):
            resp.headers['Cache-Control'] = 'no-cache, must-revalidate'
        # 半內部服務：禁止搜尋引擎索引（比 robots.txt 更強，連被連結也不進搜尋結果）。
        resp.headers.setdefault('X-Robots-Tag', 'noindex, nofollow, noarchive')
        resp.headers.setdefault('Content-Security-Policy', (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "img-src 'self' data: https:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
        ))
        if not is_dev:
            resp.headers.setdefault(
                'Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
        return resp

    with app.app_context():
        from . import routes
        app.register_blueprint(routes.bp)

        from . import admin_routes
        app.register_blueprint(admin_routes.bp)

        from . import project_routes
        app.register_blueprint(project_routes.bp)

    return app
