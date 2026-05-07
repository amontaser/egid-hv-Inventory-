"""Database layer — Flask-SQLAlchemy integration, schema seeding."""

import os
import logging
from contextlib import contextmanager
from datetime import datetime

logger = logging.getLogger(__name__)


def configure_db(app):
    """Configure Flask-SQLAlchemy on the Flask app."""
    from app.models import db

    basedir = os.path.abspath(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    db_path = os.getenv("DATABASE_PATH", os.path.join(basedir, "database.db"))
    database_url = os.getenv("DATABASE_URL", f"sqlite:///{db_path}")
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)


def get_db_connection():
    """Return the SQLAlchemy session. For use outside Flask request context."""
    from app.models import db

    return db.session


@contextmanager
def get_db():
    """Context manager yielding db.session. Commits on success, rolls back on error."""
    from app.models import db

    session = db.session
    try:
        yield session
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Database error: {e}")
        raise


def init_db():
    """Seed default data. Tables are created by db.create_all() in app factory."""
    _seed_defaults()
    logger.info("Database initialized successfully")


def _seed_defaults():
    """Seed default account managers, settings, and admin user."""
    from app.models import db, AccountManager, Setting, User

    session = db.session

    try:
        if session.query(AccountManager).count() == 0:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            managers = [
                AccountManager(
                    name="Ahmed Mohamed",
                    email="ahmed.mohamed@company.com",
                    phone="+1-555-0101",
                    state=1,
                    created_at=now,
                    updated_at=now,
                ),
                AccountManager(
                    name="Sara Ali",
                    email="sara.ali@company.com",
                    phone="+1-555-0102",
                    state=1,
                    created_at=now,
                    updated_at=now,
                ),
                AccountManager(
                    name="Omar Hassan",
                    email="omar.hassan@company.com",
                    phone="+1-555-0103",
                    state=1,
                    created_at=now,
                    updated_at=now,
                ),
            ]
            for m in managers:
                session.add(m)
            session.commit()
            logger.info("Seeded default account managers")
    except Exception as e:
        session.rollback()
        logger.warning(f"Could not seed account managers: {e}")

    try:
        defaults = [
            ("storage_threshold_pct", "20"),
            ("enable_email_alerts", "0"),
            ("enable_webhook_alerts", "0"),
            ("webhook_url", ""),
            ("alert_email_to", ""),
        ]
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for key, value in defaults:
            if session.query(Setting).filter_by(key=key).first() is None:
                session.add(Setting(key=key, value=value, updated_at=now))
        session.commit()
        logger.info("Seeded default settings")
    except Exception as e:
        session.rollback()
        logger.warning(f"Could not seed settings: {e}")

    try:
        from werkzeug.security import generate_password_hash

        if session.query(User).count() == 0:
            password = os.getenv("ADMIN_PASSWORD", "admin123")
            admin = User(
                username="admin",
                password_hash=generate_password_hash(password),
            )
            session.add(admin)
            session.commit()
            logger.info("Seeded default admin user")
    except Exception as e:
        session.rollback()
        logger.warning(f"Could not seed admin user: {e}")
