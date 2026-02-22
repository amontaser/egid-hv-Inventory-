"""Persist cluster storage data to SQLite."""

import logging
from datetime import datetime
from typing import List, Dict

from app.db import get_db_connection

logger = logging.getLogger(__name__)


def save_csv_volumes(csv_list: List[Dict], cluster_name: str):
    if not csv_list:
        return
    conn = get_db_connection()
    c = conn.cursor()
    for vol in csv_list:
        c.execute(
            """
            INSERT OR REPLACE INTO cluster_shared_volumes (
                name, volume_path, owner_node, state,
                total_size_gb, free_space_gb, used_space_gb, percent_used,
                maintenance_mode, redirected_access,
                vhd_count, vhd_max_size_gb, vhd_actual_size_gb,
                oversubscription_percent, oversubscription_gb,
                cluster_name, last_updated
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
            (
                vol.get("Name"),
                vol.get("VolumePath"),
                vol.get("OwnerNode"),
                vol.get("State"),
                vol.get("TotalSizeGB", 0),
                vol.get("FreeSpaceGB", 0),
                vol.get("UsedSpaceGB", 0),
                vol.get("PercentUsed", 0),
                1 if vol.get("MaintenanceMode") else 0,
                1 if vol.get("RedirectedAccess") else 0,
                vol.get("VHDCount", 0),
                vol.get("VHDMaxSizeGB", 0),
                vol.get("VHDActualSizeGB", 0),
                vol.get("OversubscriptionPercent", 0),
                vol.get("OversubscriptionGB", 0),
                cluster_name,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
    conn.commit()
    conn.close()
    logger.info(f"Saved {len(csv_list)} CSV volumes for cluster '{cluster_name}'")
