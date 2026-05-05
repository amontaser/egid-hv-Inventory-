"""Database compatibility helpers for SQLite / PostgreSQL dual support."""

import os


def _detect_postgres():
    url = os.getenv("DATABASE_URL", "")
    if "postgresql" in url:
        return True
    db_path = os.getenv("DATABASE_PATH", "")
    if db_path and not db_path.startswith("/") and "postgres" in db_path:
        return True
    return False


_USE_POSTGRES = _detect_postgres()


def str_agg(col, sep=",", distinct=False):
    d = "DISTINCT " if distinct else ""
    if _USE_POSTGRES:
        cast = col if "::text" in col else f"{col}::text"
        return f"STRING_AGG({d}{cast}, '{sep}')"
    return f"GROUP_CONCAT({d}{col})"


def bool_val(val=True):
    if _USE_POSTGRES:
        return "TRUE" if val else "FALSE"
    return 1 if val else 0


def bool_eq(col, val=True):
    if _USE_POSTGRES:
        return f"{col} IS {'TRUE' if val else 'FALSE'}"
    return f"{col} = {1 if val else 0}"
