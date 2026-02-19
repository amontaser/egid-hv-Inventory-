"""Flask application factory"""

import os
from flask import Flask
from flask_bootstrap import Bootstrap5
from dotenv import load_dotenv
import logging

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()


def create_app():
    """Create and configure Flask application."""
    app = Flask(
        __name__, template_folder="../../templates", static_folder="../../static"
    )

    # Configuration
    app.secret_key = os.getenv("SECRET_KEY", os.urandom(32).hex())
    app.config.update(
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=3600,  # 60 minutes
    )

    # Add sorted function to Jinja2 globals for template use
    app.jinja_env.globals["sorted"] = sorted

    # Initialize extensions
    Bootstrap5(app)

    # Register blueprints
    from app.routes import api, vms, clients, storage, auth

    app.register_blueprint(api.bp, url_prefix="/api")
    app.register_blueprint(vms.bp)
    app.register_blueprint(clients.bp)
    app.register_blueprint(storage.bp)
    app.register_blueprint(auth.auth_bp)

    # Initialize database
    from app.utils.db import init_db

    with app.app_context():
        init_db()

    return app
