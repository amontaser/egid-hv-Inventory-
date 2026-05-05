"""Database compatibility helpers for SQLite / PostgreSQL dual support."""

import os

_USE_POSTGRES = "postgresql" in os.getenv("DATABASE_URL", "sqlite")


def bool_true():
    """Return SQL for 'is true' check. Works in both SQLite 3.23+ and PostgreSQL."""
    return "IS TRUE"


def str_agg(col, sep=",", distinct=False):
    """Return the right string aggregation SQL for current database."""
    d = "DISTINCT " if distinct else ""
    if _USE_POSTGRES:
        cast = col if "::text" in col else f"{col}::text"
        return f"STRING_AGG({d}{cast}, '{sep}')"
    return f"GROUP_CONCAT({d}{col})"


def bool_val(val=True):
    """Return a boolean literal value for the current database."""
    if _USE_POSTGRES:
        return "TRUE" if val else "FALSE"
    return 1 if val else 0


def bool_eq(col, val=True):
    """Return boolean comparison compatible with both databases."""
    if _USE_POSTGRES:
        return f"{col} IS {'TRUE' if val else 'FALSE'}"
    return f"{col} = {1 if val else 0}"
