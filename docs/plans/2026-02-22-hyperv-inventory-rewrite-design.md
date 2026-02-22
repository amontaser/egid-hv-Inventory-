# Hyper-V Inventory — Full Rewrite Design

**Date:** 2026-02-22
**Status:** Approved
**Approach:** Option A — Clean Layered Rewrite (same stack: Flask + Celery + Redis + SQLite + Gunicorn)

---

## 1. Problem Statement

The existing codebase collects Hyper-V inventory data via WinRM/PowerShell but has several broken or missing pieces:

- Change detection is not implemented — `vm_history` and `notifications` tables exist but are never populated
- No monitoring for VM state changes, resource changes, or storage alerts
- Physical disk/LUN collection is missing (only CSVs are collected)
- The CSV PowerShell script has a path resolution bug
- `Get-WmiObject` used instead of modern `Get-CimInstance`
- Code is spread across mixed patterns with duplicated DB utility functions

---

## 2. Goals

1. Full cluster inventory: VMs (state, CPU, RAM, IPs, VLANs, disks), hosts (hardware, OS), Cluster Shared Volumes, physical disks per host
2. Change monitoring: detect VM state changes, VM add/delete, CPU/RAM changes, IP changes, disk size changes, low storage — generate notifications
3. Notification delivery: in-app bell, email (SMTP), webhook (Slack/Teams/generic)
4. Clean, testable architecture with clear separation of concerns

---

## 3. Architecture

### Stack (unchanged)
- **Web:** Flask 3.x + Bootstrap5 + Gunicorn
- **Task queue:** Celery 5.x + Redis (broker + result backend)
- **Database:** SQLite (via sqlite3)
- **WinRM:** pywinrm + NTLM/SSL auth, Fernet-encrypted credentials

### Layer separation

| Layer | Location | Responsibility |
|-------|----------|----------------|
| Collectors | `tasks/collectors/` | WinRM sessions + PowerShell scripts → raw Python dicts |
| Persistence | `tasks/persistence/` | Raw dicts → DB upserts |
| Monitor | `tasks/monitor.py` | Old snapshot vs new data → `vm_history` + `notifications` |
| Notifiers | `notifications/` | In-app write + email send + webhook POST |
| Web | `app/routes/` | Flask blueprints serving UI + REST API |
| DB utils | `app/db/` | Schema init, migrations, connection factory |

---

## 4. File Layout

```
/opt/hyperv_inventory/
├── app/
│   ├── __init__.py
│   ├── db/
│   │   ├── __init__.py         # init_db(), get_db(), get_db_connection()
│   │   ├── schema.sql          # All CREATE TABLE statements
│   │   └── migrations.py       # _run_migrations(), _add_column()
│   ├── routes/
│   │   ├── api.py              # REST: sync trigger, status, VM/host/storage data
│   │   ├── vms.py
│   │   ├── hosts.py
│   │   ├── storage.py
│   │   ├── clusters.py
│   │   ├── clients.py
│   │   ├── notifications.py
│   │   ├── settings.py
│   │   └── auth.py
│   └── models.py
│
├── tasks/
│   ├── __init__.py
│   ├── orchestrator.py         # fetch_hyperv_data() Celery task
│   ├── collectors/
│   │   ├── __init__.py
│   │   ├── winrm.py            # create_session(), run_ps(), run_ps_long()
│   │   ├── vms.py              # collect_vms/disks/networks/snapshots/replication
│   │   ├── hosts.py            # collect_host_info(), collect_physical_disks()
│   │   └── storage.py          # collect_csv_volumes()
│   ├── persistence/
│   │   ├── __init__.py
│   │   ├── vms.py              # save_vms/disks/networks/snapshots/replication
│   │   ├── hosts.py            # save_host(), save_physical_disks()
│   │   └── storage.py          # save_csv_volumes()
│   └── monitor.py              # detect_changes(), generate_notifications()
│
├── notifications/
│   ├── __init__.py
│   ├── email.py                # send_email_alert()
│   └── webhook.py              # send_webhook()
│
├── celeryconfig.py
├── gunicorn.conf.py
├── requirements.txt
└── templates/
```

---

## 5. Data Collected

### Per VM (collected from each host node via `fetch_single_host`)

| Field | PowerShell source |
|-------|-------------------|
| VMId, Name, State | `Get-VM` |
| CPUCount | `ProcessorCount` |
| MemoryAssigned/Demand/Startup/Min/Max | `MemoryAssigned`, `MemoryDemand`, etc. |
| DynamicMemory | `DynamicMemoryEnabled` |
| Generation, Version | `Generation`, `Version` |
| CreatedTime | `CreationTime` |
| VirtualHardDiskPath, VirtualMachinePath | `ConfigurationLocation` |
| ClusterName | `Get-Cluster` |

**Per VM disk:** DiskPath, DiskFormat, SizeGB, ControllerType/Number/Location
(using `Get-VMHardDiskDrive` + `Get-VHD`)

**Per VM network adapter:** AdapterName, SwitchName, MacAddress, IPAddresses, VlanId, IsConnected
(using `Get-VMNetworkAdapter` + `Get-VMNetworkAdapterVlan`)

**Per VM snapshot:** SnapshotName, Type, CreationTime, ParentSnapshot
**Per VM replication:** State, Health, Mode, PrimaryServer, ReplicaServer, LastReplicationTime

### Per Host (from each node)

| Field | Source |
|-------|--------|
| HostName, ClusterName | `$env:COMPUTERNAME`, `Get-Cluster` |
| TotalMemoryGB, AvailableMemoryGB | `Get-CimInstance Win32_OperatingSystem` |
| LogicalProcessors | `Get-CimInstance Win32_Processor` |
| VMCount | `(Get-VM).Count` |
| OSVersion | `Win32_OperatingSystem.Caption` |
| HyperVVersion | `Get-VMHost` |

**Physical Disks per host:**
FriendlyName, SerialNumber, MediaType (SSD/HDD/SCM), SizeGB, HealthStatus, OperationalStatus, BusType
(using `Get-PhysicalDisk` joined with `Get-Disk` for partition style and number)

### Per Cluster (one connection to cluster FQDN)

**Cluster Shared Volumes:**
Name, VolumePath, OwnerNode, State, TotalGB, FreeGB, UsedGB, PercentUsed,
VHDCount, VHDMaxGB, VHDActualGB, OversubscriptionPercent, OversubscriptionGB

---

## 6. Sync Orchestration

```
fetch_hyperv_data()                        [orchestrator]
  ├── For each enabled cluster:
  │   └── discover_cluster_nodes()         [WinRM → Get-ClusterNode → IP resolution]
  │
  ├── chord(group([
  │   ├── fetch_single_host(ip, cluster_id)  [per node]
  │   └── fetch_cluster_csv(cluster_id)      [per cluster]
  │ ]))
  │
  └── aggregate_and_monitor(results)       [chord callback — NEW]
      ├── compute_changes(old_snapshot, new_data)
      ├── write vm_history rows
      └── dispatch_notifications(changes)
```

**Scheduling:** Celery Beat — full sync daily at midnight. Manual trigger via `/api/sync` endpoint.

---

## 7. Change Detection

Before each sync, `monitor.py` snapshots current VM states and CSV free space from DB.
After sync completes, it compares:

| Change | Severity | Notification message template |
|--------|----------|-------------------------------|
| VM state changed | warning | "{vm}: {old_state} → {new_state}" |
| VM deleted | critical | "VM {vm} no longer visible on {cluster}" |
| VM created | info | "New VM {vm} discovered on {host}" |
| CPU count changed | warning | "{vm}: CPU {old} → {new}" |
| Memory changed | warning | "{vm}: RAM {old}GB → {new}GB" |
| IP address changed | info | "{vm}: IP {old} → {new}" |
| Disk count changed | info | "{vm}: disk count {old} → {new}" |
| Disk size grown | info | "{vm}: disk {path} {old}GB → {new}GB" |
| CSV free space < threshold | warning | "CSV {name}: {pct}% free (threshold {threshold}%)" |

Low-storage threshold configurable via settings (default 20%).

---

## 8. Notification Delivery

All notifications are always written to `notifications` DB table (in-app).

**Email:** If `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_EMAIL_TO` env vars are set, send via `smtplib` with TLS.

**Webhook:** If `WEBHOOK_URL` env var is set, POST JSON:
```json
{
  "type": "vm_state_change",
  "severity": "warning",
  "message": "VM prod-web-01: Running → Off",
  "vm_id": "...",
  "cluster": "PROD-CLUSTER",
  "timestamp": "2026-02-22T12:00:00"
}
```

Settings page allows configuring webhook URL, email recipients, and storage threshold.

---

## 9. Database Schema Changes

New tables / columns added vs current schema:

- `hyperv_hosts`: add `hyperv_version TEXT`, `vhd_default_path TEXT`, `vm_default_path TEXT`
- New table: `host_physical_disks` — `(host_name, friendly_name, serial_number, media_type, size_gb, health_status, bus_type, last_updated)`
- `notifications`: add `cluster_name TEXT`
- `settings`: new table — `(key TEXT PRIMARY KEY, value TEXT)` for webhook URL, alert threshold, email config

All existing tables and data are preserved via migration runner.

---

## 10. What Is NOT Changing

- Flask blueprints structure (routes stay the same)
- HTML templates (Jinja2 templates stay the same)
- Celery + Redis setup
- SQLite database file location
- Fernet credential encryption
- WinRM NTLM/SSL transport logic
- Gunicorn config
