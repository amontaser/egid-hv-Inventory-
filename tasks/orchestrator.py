"""Celery tasks: orchestrate Hyper-V data collection, change detection, notifications."""

import logging
from datetime import datetime
from typing import Dict, List, Optional

from celery import shared_task, group, chord
from sqlalchemy import text

from app.db import get_db_connection
from app.utils.db_compat import bool_eq
from tasks.collectors.winrm import create_winrm_session, resolve_node_ip

logger = logging.getLogger(__name__)


def _get_app():
    from app import create_app

    app = create_app()
    return app


from tasks.collectors.hosts import collect_cluster_nodes, PS_GET_CLUSTER_NODES
from tasks.collectors.vms import (
    collect_vms,
    collect_disks,
    collect_networks,
    collect_snapshots,
    collect_replication,
)
from tasks.collectors.hosts import collect_host_info, collect_physical_disks
from tasks.collectors.storage import collect_csv_volumes
from tasks.persistence.vms import (
    save_vms,
    save_disks,
    save_networks,
    save_snapshots,
    save_replication,
)
from tasks.persistence.hosts import save_host, save_physical_disks
from tasks.persistence.storage import save_csv_volumes
from tasks.monitor import (
    snapshot_vm_states,
    snapshot_csv_states,
    detect_vm_changes,
    detect_storage_changes,
    persist_events,
    get_storage_threshold,
)
from tasks.notifications import dispatch_notifications  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _update_sync_metadata(status: str, **kwargs):
    try:
        session = get_db_connection()
        fields = [f"{k} = :{k}" for k in kwargs if k != "start"]
        params = {k: kwargs[k] for k in kwargs if k != "start"}
        if kwargs.get("start"):
            session.execute(
                text("""
                INSERT INTO sync_metadata
                (id, last_sync_start, last_sync_status, hosts_discovered, hosts_processed)
                VALUES (1, :last_sync_start, 'running', :hosts_discovered, 0)
                ON CONFLICT (id) DO UPDATE SET
                    last_sync_start = EXCLUDED.last_sync_start,
                    last_sync_status = EXCLUDED.last_sync_status,
                    hosts_discovered = EXCLUDED.hosts_discovered,
                    hosts_processed = EXCLUDED.hosts_processed
            """),
                {
                    "last_sync_start": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "hosts_discovered": kwargs.get("hosts_discovered", 0),
                },
            )
        else:
            fields.insert(0, "last_sync_end = :last_sync_end")
            fields.insert(1, "last_sync_status = :last_sync_status")
            params["last_sync_end"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            params["last_sync_status"] = status
            params["id"] = 1
            fields_sql = ", ".join(fields)
            session.execute(
                text(f"UPDATE sync_metadata SET {fields_sql} WHERE id = :id"), params
            )
        session.commit()
    except Exception as e:
        logger.warning(f"sync_metadata update failed: {e}")


def _clear_vm_data():
    session = get_db_connection()
    for tbl in (
        "vm_info",
        "vm_disks",
        "vm_network_adapters",
        "vm_snapshots",
        "vm_replication",
    ):
        session.execute(text(f"DELETE FROM {tbl}"))
    session.commit()


def _discover_cluster_nodes(cluster_id: int, cluster_name: str) -> Dict[str, str]:
    """Connect to cluster FQDN, run Get-ClusterNode, resolve IPs. Returns {node_name: ip}."""
    session = get_db_connection()
    row = session.execute(
        text(
            "SELECT domain, dns_servers, domain_name FROM clusters WHERE id = :cluster_id"
        ),
        {"cluster_id": cluster_id},
    ).fetchone()

    domain = (row._mapping["domain"] if row else None) or cluster_name
    dns_servers = row._mapping["dns_servers"] if row else None
    domain_name = row._mapping["domain_name"] if row else None

    logger.info(f"Connecting to cluster FQDN: {domain}")
    session = create_winrm_session(domain, cluster_id=cluster_id)
    nodes = collect_cluster_nodes(session)

    if not nodes:
        raise RuntimeError(f"Get-ClusterNode returned no results from {domain}")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    node_map = {}
    for node in nodes:
        if node.get("State") == "Down":
            continue
        name = node.get("Name", "")
        ip = node.get("IPAddress") or resolve_node_ip(name, dns_servers, domain_name)
        node_map[name] = ip

    try:
        session = get_db_connection()
        for node in nodes:
            name = node.get("Name", "")
            state = node.get("State", "Unknown")
            ip = node.get("IPAddress") or ""
            session.execute(
                text("""INSERT INTO cluster_nodes (cluster_name, node_name, node_state, ip_address, last_updated)
                   VALUES (:cluster_name, :node_name, :node_state, :ip_address, :last_updated)
                   ON CONFLICT (cluster_name, node_name) DO UPDATE SET
                       node_state = EXCLUDED.node_state,
                       ip_address = EXCLUDED.ip_address,
                       last_updated = EXCLUDED.last_updated"""),
                {
                    "cluster_name": cluster_name,
                    "node_name": name,
                    "node_state": state,
                    "ip_address": ip,
                    "last_updated": now,
                },
            )
        session.commit()
    except Exception as e:
        logger.warning(f"Failed to save node states for {cluster_name}: {e}")

    if not node_map:
        raise RuntimeError(f"No Up nodes in cluster {cluster_name}")
    return node_map


# ---------------------------------------------------------------------------
# Celery tasks (names kept for backward compatibility)
# ---------------------------------------------------------------------------


@shared_task(bind=True, name="tasks.sync.fetch_hyperv_data")
def fetch_hyperv_data(self):
    """Top-level orchestrator: discover nodes, dispatch per-host + per-cluster tasks."""
    app = _get_app()
    with app.app_context():
        return _fetch_hyperv_data_impl()


def _fetch_hyperv_data_impl():
    logger.info("Starting Hyper-V inventory sync")

    session = get_db_connection()
    clusters = session.execute(
        text(
            f"SELECT id, cluster_name, domain FROM clusters WHERE {bool_eq('is_enabled')}"
        )
    ).fetchall()

    if not clusters:
        msg = "No enabled clusters configured"
        logger.error(msg)
        _update_sync_metadata("error", errors=msg)
        return {"status": "error", "message": msg}

    vm_snapshot = snapshot_vm_states()
    csv_snapshot = snapshot_csv_states()
    logger.info(f"Snapshot: {len(vm_snapshot)} VMs, {len(csv_snapshot)} CSV volumes")

    task_sigs = []
    cluster_info = []
    node_to_cluster = {}
    total_nodes = 0

    for row in clusters:
        cluster_id, cluster_name = row._mapping["id"], row._mapping["cluster_name"]
        try:
            node_map = _discover_cluster_nodes(cluster_id, cluster_name)
            node_ips = list(node_map.values())
            cluster_info.append((cluster_id, cluster_name, node_ips))
            total_nodes += len(node_ips)
            for ip in node_ips:
                node_to_cluster[ip] = cluster_id
            logger.info(f"Cluster {cluster_name}: {len(node_ips)} node(s)")
        except Exception as e:
            logger.error(f"Node discovery failed for {cluster_name}: {e}")

    if total_nodes == 0:
        msg = "No nodes discovered from any cluster"
        logger.error(msg)
        _update_sync_metadata("error", errors=msg)
        return {"status": "error", "message": msg}

    _update_sync_metadata("running", start=True, hosts_discovered=total_nodes)
    _clear_vm_data()

    for ip, cid in node_to_cluster.items():
        task_sigs.append(fetch_single_host.s(ip, cid))
    for cid, cname, node_ips in cluster_info:
        task_sigs.append(fetch_cluster_csv_storage.s(cid, cname, node_ips))

    callback = aggregate_and_monitor.s(
        vm_snapshot=vm_snapshot, csv_snapshot=csv_snapshot
    )

    try:
        chord(group(task_sigs))(callback)
        return {
            "status": "started",
            "nodes": total_nodes,
            "clusters": len(cluster_info),
        }
    except Exception as e:
        logger.error(f"Failed to dispatch sync tasks: {e}")
        _update_sync_metadata("error", errors=str(e))
        return {"status": "error", "message": str(e)}


@shared_task(name="tasks.sync.fetch_single_host")
def fetch_single_host(host_ip: str, cluster_id: int = None):
    """Collect all data from one Hyper-V host node."""
    app = _get_app()
    with app.app_context():
        return _fetch_single_host_impl(host_ip, cluster_id)


def _fetch_single_host_impl(host_ip: str, cluster_id: int = None):
    logger.info(f"[Host] Collecting from {host_ip}")

    cluster_name = None
    if cluster_id:
        try:
            session = get_db_connection()
            row = session.execute(
                text("SELECT cluster_name FROM clusters WHERE id = :cluster_id"),
                {"cluster_id": cluster_id},
            ).fetchone()
            if row:
                cluster_name = row._mapping["cluster_name"]
        except Exception:
            pass

    try:
        logger.info(f"[Host] Starting collection for {host_ip}")
        session = create_winrm_session(host_ip, cluster_id=cluster_id)

        logger.info(f"[Host] Collecting VMs from {host_ip}")
        vms = collect_vms(session)
        save_vms(vms, host_ip, cluster_name=cluster_name)

        logger.info(f"[Host] Collecting disks from {host_ip}")
        save_disks(collect_disks(session), cluster_name=cluster_name)

        logger.info(f"[Host] Collecting networks from {host_ip}")
        save_networks(collect_networks(session), cluster_name=cluster_name)

        logger.info(f"[Host] Collecting snapshots from {host_ip}")
        save_snapshots(collect_snapshots(session), cluster_name=cluster_name)

        logger.info(f"[Host] Collecting replication from {host_ip}")
        save_replication(collect_replication(session), cluster_name=cluster_name)

        logger.info(f"[Host] Collecting host info from {host_ip}")
        host_info = collect_host_info(session)

        if not host_info:
            logger.warning(
                f"[Host] Failed to collect detailed info from {host_ip}, using minimal info"
            )
            effective_hostname = (
                vms[0].get("ComputerName") if vms else None
            ) or f"HOST-{host_ip.replace('.', '-')}"
            try:
                session = get_db_connection()
                vm_count_result = session.execute(
                    text(
                        "SELECT COUNT(*) as vm_count FROM vm_info WHERE host_name = :host_name"
                    ),
                    {"host_name": effective_hostname},
                ).fetchone()
                vm_count = (
                    vm_count_result._mapping["vm_count"] if vm_count_result else 0
                )

                minimal_host_info = {
                    "HostName": effective_hostname,
                    "ClusterName": cluster_name if cluster_name else "Unknown",
                    "TotalMemoryGB": 0,
                    "AvailableMemoryGB": 0,
                    "LogicalProcessors": 0,
                    "VMCount": vm_count,
                    "OSVersion": "Unknown",
                    "HyperVVersion": None,
                    "VirtualHardDiskPath": None,
                    "VirtualMachinePath": None,
                }
                logger.info(
                    f"[Host] Created minimal host info for {host_ip} ({effective_hostname}) with {vm_count} VMs"
                )
                save_host(
                    minimal_host_info, cluster_name=cluster_name, connection_ip=host_ip
                )
            except Exception as e:
                logger.error(
                    f"[Host] Failed to create minimal host info for {host_ip}: {e}"
                )
        else:
            save_host(host_info, cluster_name=cluster_name, connection_ip=host_ip)

        logger.info(f"[Host] Collecting physical disks from {host_ip}")
        phys_disks = collect_physical_disks(session)
        if host_info and phys_disks:
            save_physical_disks(phys_disks, host_info.get("HostName", host_ip))

        try:
            session = get_db_connection()
            session.execute(
                text(
                    "UPDATE sync_metadata SET hosts_processed = hosts_processed + 1 WHERE id = 1"
                )
            )
            session.commit()
        except Exception:
            pass

        logger.info(f"[Host] Done: {host_ip} — {len(vms)} VMs")
        return {"status": "success", "host": host_ip, "vms": len(vms)}

    except Exception as e:
        logger.error(f"[Host] Error on {host_ip}: {e}")
        return {"status": "error", "host": host_ip, "error": str(e)}


@shared_task(name="tasks.sync.fetch_cluster_csv_storage")
def fetch_cluster_csv_storage(cluster_id: int, cluster_name: str, nodes: list):
    """Scan CSV volumes for a cluster (tries each node until one succeeds)."""
    app = _get_app()
    with app.app_context():
        return _fetch_cluster_csv_storage_impl(cluster_id, cluster_name, nodes)


def _fetch_cluster_csv_storage_impl(cluster_id: int, cluster_name: str, nodes: list):
    import os

    logger.info(f"[CSV] Scanning cluster {cluster_name}")

    if not nodes:
        return {"status": "error", "cluster": cluster_name, "error": "no nodes"}

    cache_seconds = int(os.getenv("CSV_CACHE_SECONDS", "3600"))
    if not _should_rescan_csv(cluster_id, cache_seconds):
        return {"status": "skipped", "cluster": cluster_name}

    for node in nodes:
        try:
            session = create_winrm_session(node, cluster_id=cluster_id)
            csv_list = collect_csv_volumes(session)
            save_csv_volumes(csv_list, cluster_name)
            logger.info(f"[CSV] {cluster_name}: {len(csv_list)} volumes from {node}")
            _update_csv_scan_metadata(cluster_id, "success", vol_count=len(csv_list))
            return {
                "status": "success",
                "cluster": cluster_name,
                "volumes": len(csv_list),
                "node": node,
            }
        except Exception as e:
            logger.warning(f"[CSV] Node {node} failed: {e}")
            continue

    error_msg = f"All nodes failed for cluster {cluster_name}"
    logger.error(error_msg)
    _update_csv_scan_metadata(cluster_id, "error", error=error_msg)
    return {"status": "error", "cluster": cluster_name, "error": error_msg}


@shared_task(name="tasks.sync.aggregate_sync_results_with_csv")
def aggregate_and_monitor(results, vm_snapshot=None, csv_snapshot=None):
    """Chord callback: aggregate results, run change detection, dispatch notifications."""
    app = _get_app()
    with app.app_context():
        return _aggregate_and_monitor_impl(results, vm_snapshot, csv_snapshot)


def _aggregate_and_monitor_impl(results, vm_snapshot=None, csv_snapshot=None):
    logger.info(f"Aggregating {len(results)} task results")

    total_vms = sum(
        r.get("vms", 0)
        for r in results
        if isinstance(r, dict) and r.get("status") == "success"
    )
    errors = [
        str(err)
        for err in (
            r.get("error")
            for r in results
            if isinstance(r, dict) and r.get("status") == "error"
        )
        if err
    ]

    if vm_snapshot is not None:
        threshold = get_storage_threshold()
        vm_events = detect_vm_changes(vm_snapshot)
        storage_events = detect_storage_changes(
            csv_snapshot or {}, threshold_pct=threshold
        )
        all_events = vm_events + storage_events

        if all_events:
            persist_events(all_events)
            dispatch_notifications(all_events)
            logger.info(
                f"Detected {len(all_events)} changes ({len(vm_events)} VM, {len(storage_events)} storage)"
            )

    status = "success" if not errors else "partial"
    _update_sync_metadata(
        status, vms_synced=total_vms, errors="; ".join(errors) if errors else None
    )
    logger.info(f"Sync complete: {total_vms} VMs, status={status}")
    return {"status": status, "vms_synced": total_vms}


def _should_rescan_csv(cluster_id: int, cache_seconds: int) -> bool:
    try:
        from datetime import datetime

        session = get_db_connection()
        row = session.execute(
            text(
                "SELECT last_scan_end FROM csv_scan_metadata WHERE cluster_id = :cluster_id"
            ),
            {"cluster_id": cluster_id},
        ).fetchone()
        if not row or not row[0]:
            return True
        last = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
        return (datetime.now() - last).total_seconds() > cache_seconds
    except Exception:
        return True


def _update_csv_scan_metadata(
    cluster_id: int, status: str, vol_count: int = 0, error: str = None
):
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        session = get_db_connection()
        session.execute(
            text("""
            INSERT INTO csv_scan_metadata
            (cluster_id, last_scan_end, scan_status, volumes_scanned, errors)
            VALUES (:cluster_id, :last_scan_end, :scan_status, :volumes_scanned, :errors)
            ON CONFLICT DO NOTHING
        """),
            {
                "cluster_id": cluster_id,
                "last_scan_end": now,
                "scan_status": status,
                "volumes_scanned": vol_count,
                "errors": error,
            },
        )
        session.commit()
    except Exception as e:
        logger.warning(f"csv_scan_metadata update failed: {e}")
