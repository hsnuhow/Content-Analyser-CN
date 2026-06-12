import os
from flask import Flask
from authlib.integrations.flask_client import OAuth
from werkzeug.middleware.proxy_fix import ProxyFix
from . import services # Initialize Firebase

oauth = OAuth()

def create_app():
    app = Flask(__name__)

    # Apply ProxyFix middleware
    # x_proto=1: Trust X-Forwarded-Proto (https/http)
    # x_host=1: Trust X-Forwarded-Host
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    # Basic Config
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev_secret_key')
    app.config['GOOGLE_CLIENT_ID'] = os.environ.get('GOOGLE_CLIENT_ID')
    app.config['GOOGLE_CLIENT_SECRET'] = os.environ.get('GOOGLE_CLIENT_SECRET')
    
    # [Fix] Session Cookie Configuration for Preview/Dev
    # In production (Cloud Run), we want Secure cookies.
    # In development (Preview), we need to relax this to allow cookies over HTTP or complex proxies.
    is_dev = os.environ.get('FLASK_DEBUG') == '1'
    
    if is_dev:
        app.config['SESSION_COOKIE_SECURE'] = False
        app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    else:
        app.config['SESSION_COOKIE_SECURE'] = True
        app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    
    # Initialize OAuth
    oauth.init_app(app)
    
    # Register Google OAuth
    oauth.register(
        name='google',
        client_id=app.config['GOOGLE_CLIENT_ID'],
        client_secret=app.config['GOOGLE_CLIENT_SECRET'],
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'}
    )

    with app.app_context():
        from . import routes
        app.register_blueprint(routes.bp)

        from . import admin_routes
        app.register_blueprint(admin_routes.bp)

        from . import project_routes
        app.register_blueprint(project_routes.bp)

    return app
