"""Schema migrations — ensures UNIQUE constraints match ON CONFLICT upserts."""

import logging

from sqlalchemy import inspect, text

logger = logging.getLogger(__name__)

_MIGRATIONS = [
    {
        "name": "001_unique_vm_disks",
        "check": "SELECT name FROM sqlite_master WHERE type='index' AND name='uq_vm_disks_vm_cluster_path'",
        "sql": [
            "DROP INDEX IF EXISTS idx_vm_disks_vm_id",
            "CREATE UNIQUE INDEX uq_vm_disks_vm_cluster_path ON vm_disks(vm_id, cluster_name, disk_path)",
            "CREATE INDEX idx_vm_disks_vm_id ON vm_disks(vm_id, cluster_name)",
        ],
    },
    {
        "name": "002_unique_vm_network_adapters",
        "check": "SELECT name FROM sqlite_master WHERE type='index' AND name='uq_vm_network_adapter'",
        "sql": [
            "DROP INDEX IF EXISTS idx_vm_network_vm_id",
            "CREATE UNIQUE INDEX uq_vm_network_adapter ON vm_network_adapters(vm_id, cluster_name, adapter_name, mac_address)",
            "CREATE INDEX idx_vm_network_vm_id ON vm_network_adapters(vm_id, cluster_name)",
        ],
    },
    {
        "name": "003_unique_vm_snapshots",
        "check": "SELECT name FROM sqlite_master WHERE type='index' AND name='uq_vm_snapshots'",
        "sql": [
            "DROP INDEX IF EXISTS idx_vm_snapshots_vm_id",
            "CREATE UNIQUE INDEX uq_vm_snapshots ON vm_snapshots(vm_id, cluster_name, snapshot_name)",
            "CREATE INDEX idx_vm_snapshots_vm_id ON vm_snapshots(vm_id, cluster_name)",
        ],
    },
    {
        "name": "004_unique_vm_replication",
        "check": "SELECT name FROM sqlite_master WHERE type='index' AND name='uq_vm_replication_vm_cluster'",
        "sql": [
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_vm_replication_vm_cluster ON vm_replication(vm_id, cluster_name)",
        ],
    },
]


# Columns added to existing tables after the initial schema was released.
# db.create_all() only creates missing tables, it never alters existing ones,
# so PostgreSQL databases created earlier must be migrated on startup.
_COLUMN_SPECS = {
    "vm_info": [
        ("guest_hostname", "VARCHAR(255)"),
        ("guest_fqdn", "VARCHAR(255)"),
        ("is_clustered", "BOOLEAN"),
        ("cluster_role_state", "VARCHAR(50)"),
        ("cluster_owner_node", "VARCHAR(255)"),
    ],
}


def _column_exists(db, table_name: str, column_name: str) -> bool:
    inspector = inspect(db.get_bind())
    return any(
        column["name"] == column_name
        for column in inspector.get_columns(table_name)
    )


def _ensure_columns(db):
    """Add any missing columns to existing tables (idempotent)."""
    added = []
    for table_name, columns in _COLUMN_SPECS.items():
        for column_name, column_type in columns:
            if _column_exists(db, table_name, column_name):
                continue
            try:
                db.execute(
                    text(
                        f"ALTER TABLE {table_name} ADD COLUMN "
                        f"{column_name} {column_type}"
                    )
                )
                db.commit()
                added.append(f"{table_name}.{column_name}")
            except Exception:
                db.rollback()
                # Another process (web/worker) may have added it concurrently.
                if not _column_exists(db, table_name, column_name):
                    raise
    if added:
        logger.info("Schema migration applied: %s", ", ".join(added))


def run_migrations(db):
    try:
        _ensure_columns(db)
    except Exception:
        db.rollback()
        logger.warning("Schema migration skipped", exc_info=True)

    for mig in _MIGRATIONS:
        try:
            result = db.execute(mig["check"]).fetchone()
            if result:
                continue
            for stmt in mig["sql"]:
                db.execute(stmt)
            db.commit()
            logger.info(f"Migration {mig['name']} applied")
        except Exception:
            db.rollback()
            logger.debug(f"Migration {mig['name']} skipped (table may not exist yet)")
