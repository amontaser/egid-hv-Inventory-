"""Persist cluster storage data to SQLite."""

import logging
from datetime import datetime
from typing import List, Dict

from sqlalchemy import text

from app.db import get_db_connection

logger = logging.getLogger(__name__)


def save_csv_volumes(csv_list: List[Dict], cluster_name: str):
    if not csv_list:
        return
    session = get_db_connection()
    for vol in csv_list:
        session.execute(
            text("""
            INSERT INTO cluster_shared_volumes (
                name, volume_path, owner_node, state,
                total_size_gb, free_space_gb, used_space_gb, percent_used,
                maintenance_mode, redirected_access,
                vhd_count, vhd_max_size_gb, vhd_actual_size_gb,
                oversubscription_percent, oversubscription_gb,
                cluster_name, last_updated
            ) VALUES (
                :name, :volume_path, :owner_node, :state,
                :total_size_gb, :free_space_gb, :used_space_gb, :percent_used,
                :maintenance_mode, :redirected_access,
                :vhd_count, :vhd_max_size_gb, :vhd_actual_size_gb,
                :oversubscription_percent, :oversubscription_gb,
                :cluster_name, :last_updated
            )
            ON CONFLICT (name, volume_path, cluster_name) DO UPDATE SET
                owner_node              = EXCLUDED.owner_node,
                state                   = EXCLUDED.state,
                total_size_gb           = EXCLUDED.total_size_gb,
                free_space_gb           = EXCLUDED.free_space_gb,
                used_space_gb           = EXCLUDED.used_space_gb,
                percent_used            = EXCLUDED.percent_used,
                maintenance_mode        = EXCLUDED.maintenance_mode,
                redirected_access       = EXCLUDED.redirected_access,
                vhd_count               = EXCLUDED.vhd_count,
                vhd_max_size_gb         = EXCLUDED.vhd_max_size_gb,
                vhd_actual_size_gb      = EXCLUDED.vhd_actual_size_gb,
                oversubscription_percent = EXCLUDED.oversubscription_percent,
                oversubscription_gb     = EXCLUDED.oversubscription_gb,
                last_updated            = EXCLUDED.last_updated
        """),
            {
                "name": vol.get("Name"),
                "volume_path": vol.get("VolumePath"),
                "owner_node": vol.get("OwnerNode"),
                "state": vol.get("State"),
                "total_size_gb": vol.get("TotalSizeGB", 0),
                "free_space_gb": vol.get("FreeSpaceGB", 0),
                "used_space_gb": vol.get("UsedSpaceGB", 0),
                "percent_used": vol.get("PercentUsed", 0),
                "maintenance_mode": bool(vol.get("MaintenanceMode")),
                "redirected_access": bool(vol.get("RedirectedAccess")),
                "vhd_count": vol.get("VHDCount", 0),
                "vhd_max_size_gb": vol.get("VHDMaxSizeGB", 0),
                "vhd_actual_size_gb": vol.get("VHDActualSizeGB", 0),
                "oversubscription_percent": vol.get("OversubscriptionPercent", 0),
                "oversubscription_gb": vol.get("OversubscriptionGB", 0),
                "cluster_name": cluster_name,
                "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
    session.commit()
    logger.info(f"Saved {len(csv_list)} CSV volumes for cluster '{cluster_name}'")
