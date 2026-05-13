"""Flask application factory"""

import os
from flask import Flask, redirect, request, jsonify
from flask_bootstrap import Bootstrap5
from flask_login import LoginManager
from dotenv import load_dotenv
import logging

logger = logging.getLogger(__name__)

load_dotenv()

login_manager = LoginManager()


def create_app():
    """Create and configure Flask application."""
    basedir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))

    app = Flask(
        __name__,
        template_folder=os.path.join(basedir, "templates"),
        static_folder=os.path.join(basedir, "static"),
    )

    app.secret_key = os.getenv("SECRET_KEY", os.urandom(32).hex())
    app.config.update(
        SESSION_COOKIE_SECURE=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=3600,
    )

    app.jinja_env.globals["sorted"] = sorted

    Bootstrap5(app)

    from app.db import configure_db, init_db
    from app.models import db

    configure_db(app)

    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    @login_manager.unauthorized_handler
    def unauthorized():
        if (
            request.accept_mimetypes.accept_json
            and not request.accept_mimetypes.accept_html
        ) or request.path.startswith("/api/"):
            return jsonify({"error": "Authentication required"}), 401
        return redirect("/login")

    from app.models import User

    from app.routes import (
        api,
        vms,
        clients,
        storage,
        auth,
        hosts,
        settings,
        clusters,
        notifications,
    )

    app.register_blueprint(api.bp, url_prefix="/api")
    app.register_blueprint(vms.bp)
    app.register_blueprint(clients.bp)
    app.register_blueprint(storage.bp)
    app.register_blueprint(auth.auth_bp)
    app.register_blueprint(hosts.bp)
    app.register_blueprint(settings.bp)
    app.register_blueprint(clusters.bp)
    app.register_blueprint(notifications.bp)

    with app.app_context():
        db.create_all()
        from app.db.migrations import run_migrations

        run_migrations(db.session)
        init_db()

    return app
