"""Backward-compatible re-export — logic now lives in app/db/."""

from app.db import get_db_connection, get_db, init_db  # noqa: F401

# Re-export migrations for test compatibility
from app.db.migrations import run_migrations as _run_migrations  # noqa: F401
