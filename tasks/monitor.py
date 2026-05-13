"""Change detection: compare old DB snapshot vs new sync data, generate notifications."""

import logging
from datetime import datetime
from typing import Dict, List

from sqlalchemy import text

from app.db import get_db_connection

logger = logging.getLogger(__name__)


def snapshot_vm_states() -> Dict[str, Dict]:
    """Capture current VM state from DB before sync overwrites it.

    Returns dict keyed by "vm_id||cluster_name" -> {vm_id, state, cpu_count, memory_gb, ip, disk_count, name, cluster}
    """
    session = get_db_connection()
    rows = session.execute(
        text("""
        SELECT vi.vm_id, vi.machine_name, vi.state, vi.cpu_count,
               vi.memory_assigned_gb, vi.cluster_name,
               (SELECT ip_addresses FROM vm_network_adapters
                WHERE vm_id = vi.vm_id AND cluster_name = vi.cluster_name AND ip_addresses != '' LIMIT 1) as ip,
               (SELECT COUNT(*) FROM vm_disks WHERE vm_id = vi.vm_id AND cluster_name = vi.cluster_name) as disk_count
        FROM vm_info vi
    """)
    ).fetchall()
    return {
        f"{r._mapping['vm_id']}||{r._mapping['cluster_name']}": {
            "vm_id": r._mapping["vm_id"],
            "name": r._mapping["machine_name"],
            "state": r._mapping["state"],
            "cpu_count": r._mapping["cpu_count"],
            "memory_gb": r._mapping["memory_assigned_gb"],
            "cluster": r._mapping["cluster_name"],
            "ip": r._mapping["ip"],
            "disk_count": r._mapping["disk_count"],
        }
        for r in rows
    }


def snapshot_csv_states() -> Dict[str, Dict]:
    """Capture current CSV free-space state before sync.

    Returns dict keyed by (name, cluster_name) tuple serialized as "name||cluster".
    """
    session = get_db_connection()
    rows = session.execute(
        text(
            "SELECT name, cluster_name, free_space_gb, percent_used FROM cluster_shared_volumes"
        )
    ).fetchall()
    return {
        f"{r._mapping['name']}||{r._mapping['cluster_name']}": {
            "name": r._mapping["name"],
            "cluster": r._mapping["cluster_name"],
            "free_gb": r._mapping["free_space_gb"],
            "pct_used": r._mapping["percent_used"],
        }
        for r in rows
    }


def detect_vm_changes(
    old_snapshot: Dict[str, Dict], storage_threshold_pct: float = 20.0
) -> List[Dict]:
    """Compare old snapshot against freshly-written vm_info rows.

    Returns list of change event dicts.
    """
    session = get_db_connection()
    new_rows = session.execute(
        text("""
        SELECT vi.vm_id, vi.machine_name, vi.state, vi.cpu_count,
               vi.memory_assigned_gb, vi.cluster_name,
               (SELECT ip_addresses FROM vm_network_adapters
                WHERE vm_id = vi.vm_id AND cluster_name = vi.cluster_name AND ip_addresses != '' LIMIT 1) as ip,
               (SELECT COUNT(*) FROM vm_disks WHERE vm_id = vi.vm_id AND cluster_name = vi.cluster_name) as disk_count
        FROM vm_info vi
    """)
    ).fetchall()

    new_snapshot = {
        f"{r._mapping['vm_id']}||{r._mapping['cluster_name']}": {
            "vm_id": r._mapping["vm_id"],
            "name": r._mapping["machine_name"],
            "state": r._mapping["state"],
            "cpu_count": r._mapping["cpu_count"],
            "memory_gb": r._mapping["memory_assigned_gb"],
            "cluster": r._mapping["cluster_name"],
            "ip": r._mapping["ip"],
            "disk_count": r._mapping["disk_count"],
        }
        for r in new_rows
    }

    events = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for key, new in new_snapshot.items():
        if key not in old_snapshot:
            events.append(
                {
                    "vm_id": new["vm_id"],
                    "machine_name": new["name"],
                    "change_type": "created",
                    "field_name": None,
                    "old_value": None,
                    "new_value": new["state"],
                    "cluster_name": new["cluster"],
                    "severity": "info",
                    "message": f"New VM '{new['name']}' discovered on cluster {new['cluster']}",
                    "detected_at": now,
                }
            )

    for key, old in old_snapshot.items():
        if key not in new_snapshot:
            events.append(
                {
                    "vm_id": old["vm_id"],
                    "machine_name": old["name"],
                    "change_type": "deleted",
                    "field_name": None,
                    "old_value": old["state"],
                    "new_value": None,
                    "cluster_name": old["cluster"],
                    "severity": "critical",
                    "message": f"VM '{old['name']}' no longer visible on cluster {old['cluster']}",
                    "detected_at": now,
                }
            )

    for key, new in new_snapshot.items():
        if key not in old_snapshot:
            continue
        old = old_snapshot[key]

        checks = [
            (
                "state",
                "state_change",
                "warning",
                lambda o, n: f"VM '{new['name']}': state {o} → {n}",
            ),
            (
                "cpu_count",
                "cpu",
                "warning",
                lambda o, n: f"VM '{new['name']}': CPU {o} → {n}",
            ),
            (
                "memory_gb",
                "memory",
                "warning",
                lambda o, n: f"VM '{new['name']}': RAM {o}GB → {n}GB",
            ),
            (
                "ip",
                "ip_change",
                "info",
                lambda o, n: f"VM '{new['name']}': IP {o} → {n}",
            ),
            (
                "disk_count",
                "disk_change",
                "info",
                lambda o, n: f"VM '{new['name']}': disk count {o} → {n}",
            ),
        ]
        for field, change_type, severity, msg_fn in checks:
            old_val = old.get(field)
            new_val = new.get(field)
            if old_val != new_val and not (old_val is None and new_val is None):
                events.append(
                    {
                        "vm_id": new["vm_id"],
                        "machine_name": new["name"],
                        "change_type": change_type,
                        "field_name": field,
                        "old_value": str(old_val),
                        "new_value": str(new_val),
                        "cluster_name": new["cluster"],
                        "severity": severity,
                        "message": msg_fn(old_val, new_val),
                        "detected_at": now,
                    }
                )

    return events


def detect_storage_changes(
    old_csv_snapshot: Dict[str, Dict], threshold_pct: float = 20.0
) -> List[Dict]:
    """Check current CSVs for low free space."""
    session = get_db_connection()
    rows = session.execute(
        text(
            "SELECT name, cluster_name, free_space_gb, percent_used FROM cluster_shared_volumes"
        )
    ).fetchall()

    events = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    free_threshold = 100.0 - threshold_pct

    for row in rows:
        pct_used = row._mapping["percent_used"] or 0
        if pct_used >= free_threshold:
            pct_free = round(100 - pct_used, 1)
            events.append(
                {
                    "vm_id": None,
                    "machine_name": row._mapping["name"],
                    "change_type": "storage_low",
                    "field_name": "percent_used",
                    "old_value": None,
                    "new_value": str(pct_used),
                    "cluster_name": row._mapping["cluster_name"],
                    "severity": "warning",
                    "message": (
                        f"CSV '{row._mapping['name']}' on cluster {row._mapping['cluster_name']}: "
                        f"only {pct_free}% free (threshold {threshold_pct}%)"
                    ),
                    "detected_at": now,
                }
            )

    return events


def persist_events(events: List[Dict]):
    """Write change events to vm_history and notifications tables."""
    if not events:
        return
    session = get_db_connection()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for ev in events:
        if ev["change_type"] != "storage_low" and ev.get("vm_id"):
            try:
                session.execute(
                    text("""
                        INSERT INTO vm_history
                        (vm_id, cluster_name, machine_name, change_type, field_name, old_value, new_value,
                         change_description, detected_at)
                        VALUES (:vm_id, :cluster_name, :machine_name, :change_type, :field_name, :old_value, :new_value,
                                :change_description, :detected_at)
                        ON CONFLICT DO NOTHING
                    """),
                    {
                        "vm_id": ev["vm_id"],
                        "cluster_name": ev.get("cluster_name"),
                        "machine_name": ev["machine_name"],
                        "change_type": ev["change_type"],
                        "field_name": ev.get("field_name"),
                        "old_value": ev.get("old_value"),
                        "new_value": ev.get("new_value"),
                        "change_description": ev["message"],
                        "detected_at": ev["detected_at"],
                    },
                )
            except Exception as e:
                logger.warning(f"Could not write vm_history: {e}")

        session.execute(
            text("""
                INSERT INTO notifications
                (notification_type, vm_id, machine_name, message, severity, cluster_name, is_read, created_at)
                VALUES (:notification_type, :vm_id, :machine_name, :message, :severity, :cluster_name, FALSE, :created_at)
            """),
            {
                "notification_type": ev["change_type"],
                "vm_id": ev.get("vm_id"),
                "machine_name": ev["machine_name"],
                "message": ev["message"],
                "severity": ev["severity"],
                "cluster_name": ev.get("cluster_name"),
                "created_at": now,
            },
        )

    session.commit()
    logger.info(f"Persisted {len(events)} change events")


def get_storage_threshold() -> float:
    """Read low-storage threshold from settings table (default 20%)."""
    try:
        session = get_db_connection()
        row = session.execute(
            text("SELECT value FROM settings WHERE key='storage_threshold_pct'")
        ).fetchone()
        return float(row._mapping["value"]) if row else 20.0
    except Exception:
        return 20.0
