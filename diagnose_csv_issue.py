#!/usr/bin/env python3
"""Diagnostic script to troubleshoot CSV collection issues for specific clusters."""

import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

from tasks.collectors.winrm import create_winrm_session
from tasks.collectors.storage import PS_GET_CSV_INFO
from tasks.collectors.winrm import run_ps_long


def diagnose_cluster(
    cluster_id: int, cluster_name: str, domain: str, username: str, test_node: str
):
    """Run diagnostics on a specific cluster node."""

    logger.info(f"\n{'=' * 60}")
    logger.info(f"DIAGNOSING CLUSTER: {cluster_name}")
    logger.info(f"Cluster ID: {cluster_id}")
    logger.info(f"Domain: {domain}")
    logger.info(f"Test Node: {test_node}")
    logger.info(f"{'=' * 60}\n")

    # Test 1: Basic connectivity
    logger.info("Test 1: Testing WinRM connectivity...")
    try:
        session = create_winrm_session(test_node, cluster_id=cluster_id)
        logger.info("✓ WinRM session created successfully\n")
    except Exception as e:
        logger.error(f"✗ WinRM connection failed: {e}\n")
        return

    # Test 2: Check if Failover Clustering module is available
    logger.info("Test 2: Checking Failover Clustering module availability...")
    ps_check_module = """
    $ProgressPreference = 'SilentlyContinue'
    $ErrorActionPreference = 'SilentlyContinue'

    $module = Get-Module -ListAvailable -Name FailoverClusters
    if ($module) {
        @{
            Available = $true
            Version = $module.Version.ToString()
            Path = $module.Path
        }
    } else {
        @{
            Available = $false
            Version = $null
            Path = $null
        }
    }
    """

    try:
        result = run_ps_long(session, ps_check_module, context="check_module")
        if result and len(result) > 0:
            if result[0].get("Available"):
                logger.info(f"✓ Failover Clustering module available")
                logger.info(f"  Version: {result[0].get('Version')}")
                logger.info(f"  Path: {result[0].get('Path')}\n")
            else:
                logger.warning(
                    "✗ Failover Clustering module NOT available on this node\n"
                )
        else:
            logger.warning("✗ Could not determine module availability\n")
    except Exception as e:
        logger.error(f"✗ Error checking module: {e}\n")

    # Test 3: Check if node is in a cluster
    logger.info("Test 3: Checking if this node is a cluster member...")
    ps_check_cluster = """
    $ProgressPreference = 'SilentlyContinue'
    $ErrorActionPreference = 'Stop'

    try {
        $cluster = Get-Cluster -ErrorAction Stop
        @{
            InCluster = $true
            ClusterName = $cluster.Name
            Domain = $cluster.Domain
        }
    } catch {
        @{
            InCluster = $false
            ClusterName = $null
            Domain = $null
            Error = $_.Exception.Message
        }
    }
    """

    try:
        result = run_ps_long(session, ps_check_cluster, context="check_cluster")
        if result and len(result) > 0:
            if result[0].get("InCluster"):
                logger.info(f"✓ Node is part of a cluster")
                logger.info(f"  Cluster: {result[0].get('ClusterName')}")
                logger.info(f"  Domain: {result[0].get('Domain')}\n")
            else:
                logger.warning("✗ Node is NOT part of any cluster")
                logger.warning(f"  Error: {result[0].get('Error')}\n")
        else:
            logger.warning("✗ Could not determine cluster membership\n")
    except Exception as e:
        logger.error(f"✗ Error checking cluster membership: {e}\n")

    # Test 4: Try to get CSVs with detailed error info
    logger.info("Test 4: Attempting to get Cluster Shared Volumes...")
    ps_get_csv_detailed = """
    $ProgressPreference = 'SilentlyContinue'
    $ErrorActionPreference = 'Continue'

    try {
        $csvs = Get-ClusterSharedVolume -ErrorAction Stop

        $result = @{
            Success = $true
            Count = $csvs.Count
            Error = $null
            Volumes = @()
        }

        foreach ($csv in $csvs) {
            $result.Volumes += @{
                Name = $csv.Name
                State = $csv.State.ToString()
                OwnerNode = if ($csv.OwnerNode) { $csv.OwnerNode.Name } else { $null }
            }
        }

        $result
    } catch {
        @{
            Success = $false
            Count = 0
            Error = $_.Exception.Message
            Volumes = @()
        }
    }
    """

    try:
        result = run_ps_long(session, ps_get_csv_detailed, context="get_csv_detailed")
        if result and len(result) > 0:
            if result[0].get("Success"):
                count = result[0].get("Count", 0)
                logger.info(f"✓ Get-ClusterSharedVolume succeeded")
                logger.info(f"  Volumes found: {count}")
                if count > 0:
                    logger.info(f"  Volume details:")
                    for vol in result[0].get("Volumes", []):
                        logger.info(
                            f"    - {vol.get('Name')}: {vol.get('State')} on {vol.get('OwnerNode')}"
                        )
                else:
                    logger.warning("  ⚠ No CSV volumes found (but command succeeded)")
            else:
                logger.error(f"✗ Get-ClusterSharedVolume failed")
                logger.error(f"  Error: {result[0].get('Error')}")
        else:
            logger.warning("✗ No result returned\n")
    except Exception as e:
        logger.error(f"✗ Error getting CSVs: {e}\n")

    # Test 5: Try the actual production script
    logger.info("\nTest 5: Running production CSV collection script...")
    try:
        result = run_ps_long(session, PS_GET_CSV_INFO, context="production_script")
        if result:
            logger.info(f"✓ Production script returned {len(result)} volumes")
            for vol in result[:3]:  # Show first 3
                logger.info(f"  - {vol.get('Name')}: {vol.get('VolumePath')}")
            if len(result) > 3:
                logger.info(f"  ... and {len(result) - 3} more")
        else:
            logger.warning("✗ Production script returned no results")
    except Exception as e:
        logger.error(f"✗ Production script failed: {e}")

    logger.info(f"\n{'=' * 60}\n")


if __name__ == "__main__":
    import sqlite3

    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row

    # Get problematic clusters
    clusters = conn.execute(
        "SELECT id, cluster_name, domain, username FROM clusters WHERE is_enabled = 1"
    ).fetchall()

    # Get recent successful nodes for each cluster
    for cluster in clusters:
        cluster_id = cluster["id"]

        # Find the most recent node that worked from logs
        log_result = conn.execute(
            """
            SELECT DISTINCT owner_node 
            FROM cluster_shared_volumes 
            WHERE cluster_name = ?
            LIMIT 1
        """,
            (cluster["cluster_name"],),
        ).fetchone()

        # If no CSVs exist, use ip_address from cluster config
        if not log_result:
            cluster_config = conn.execute(
                "SELECT ip_address FROM clusters WHERE id = ?", (cluster_id,)
            ).fetchone()
            test_node = (
                cluster_config["ip_address"] if cluster_config else cluster["domain"]
            )
        else:
            # Resolve owner_node to IP (for now, use the owner_node name)
            test_node = log_result[0]

        # For this diagnostic, we'll use the cluster domain/IP
        cluster_full = conn.execute(
            "SELECT * FROM clusters WHERE id = ?", (cluster_id,)
        ).fetchone()

        # Use domain for connection
        diagnose_cluster(
            cluster_id=cluster_full["id"],
            cluster_name=cluster_full["cluster_name"],
            domain=cluster_full["domain"],
            username=cluster_full["username"],
            test_node=cluster_full["domain"],
        )

    conn.close()
