"""Schema migrations handled by Alembic. See alembic/ directory."""

import logging

logger = logging.getLogger(__name__)


def run_migrations(db):
    logger.info("Migrations handled by Alembic (no-op)")
