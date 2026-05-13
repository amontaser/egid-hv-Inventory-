"""Schema migrations — ensures UNIQUE constraints match ON CONFLICT upserts."""

import logging

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


def run_migrations(db):
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
