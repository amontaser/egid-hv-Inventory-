"""
Database-independent SQL helpers.
"""
import logging

logger = logging.getLogger(__name__)

def get_db_engine_type():
    """Returns 'sqlite' or 'postgresql' based on the current app configuration."""
    from flask import current_app

    try:
        # Check the configured URI
        uri = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
        if uri.startswith("postgresql"):
            return "postgresql"
        return "sqlite"
    except Exception as e:
        logger.warning(f"Could not determine database engine type: {e}")
        return "sqlite"

def group_concat(column, separator=',', distinct=False):
    """
    Returns the SQL fragment for group concatenation.
    """
    engine = get_db_engine_type()
    distinct_str = "DISTINCT " if distinct else ""

    if engine == "postgresql":
        return f"STRING_AGG({distinct_str}{column}::text, '{separator}')"
    else:
        # SQLite
        return f"GROUP_CONCAT({distinct_str}{column}, '{separator}')"
