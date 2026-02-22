"""Backward-compatible re-export — logic now lives in app/db/."""

from app.db import get_db_connection, get_db, init_db  # noqa: F401
