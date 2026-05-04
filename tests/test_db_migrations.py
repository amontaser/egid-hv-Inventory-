"""Test that migration stub doesn't error."""

import pytest
import os
from app import create_app
from app.models import db as _db


@pytest.fixture
def app_ctx():
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    app = create_app()
    with app.app_context():
        yield app


def test_run_migrations_noop(app_ctx):
    from app.db.migrations import run_migrations
    from app.db import get_db_connection

    session = get_db_connection()
    run_migrations(session)


def test_migrations_idempotent(app_ctx):
    from app.db.migrations import run_migrations
    from app.db import get_db_connection

    session = get_db_connection()
    run_migrations(session)
    run_migrations(session)
