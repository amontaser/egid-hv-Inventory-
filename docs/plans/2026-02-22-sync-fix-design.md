# Hyper-V Sync Fix Design

**Date:** 2026-02-22
**Topic:** Fix parallel sync (Option C — fix current chord/group approach)

## Problem

The sync does not work. 16 bugs were found across every layer of the stack.

## Root Causes

### Database (`app/utils/db.py`)
1. `clusters` table missing columns: `domain`, `cluster_name_for_ps`, `domain_name`, `dns_servers`, `username`, `password`, `transport`, `require_https`
2. `cluster_shared_volumes` missing `cluster_name` column — `save_csv_to_db()` inserts it but column does not exist
3. `cluster_shared_volumes` UNIQUE constraint is `(name, volume_path)` — must include `cluster_name` to avoid cross-cluster collisions
4. `csv_scan_metadata` missing `cluster_id` column — referenced in `update_csv_scan_metadata()`
5. `hyperv_hosts` column mismatch: schema has `logical_processors` but `save_host_to_db()` inserts into `logical_processor_count`

### Cluster routes (`app/routes/clusters.py`)
6. `add_cluster()` and `edit_cluster()` only save `cluster_name, location, is_enabled` — all WinRM fields discarded
7. All SELECT queries hardcode `'' as domain, '' as username` etc. instead of reading stored values
8. `test_cluster_connection()` is a stub returning a fake simulated response

### Sync tasks (`tasks/sync.py`)
9. `discover_cluster_nodes()` tries to find an existing host in `hyperv_hosts` (empty on first run), falls back to `cluster_name` as WinRM hostname — never uses the `domain` field
10. `aggregate_sync_results_with_csv` is a plain function called as `.s()` — not decorated with `@shared_task`
11. `group(vm_sync_job, csv_scan_job)` creates nested groups — chord callback receives `[[...], [...]]` not a flat list
12. `PS_GET_VMS` has a PowerShell syntax error on the `VirtualHardDiskPath` line

### CSV scanner (`tasks/csv_scanner.py`)
13. `fetch_cluster_csv_storage` is a plain function — used with `.s()` which requires `@shared_task`
14. `PS_GET_CSV_INFO` uses `$UsingClusterName` — a PowerShell `$using:` remoting variable never set in this context

### Requirements
15. `dnspython` not in `requirements.txt` — needed for custom DNS resolution
16. `cryptography` not in `requirements.txt` — already used in `hyperv.py` for Fernet decryption

## Design

### Sync Flow

```
fetch_hyperv_data (Celery orchestrator task)
│
├── For each enabled cluster in DB:
│   ├── Read: domain, dns_servers, domain_name, credentials
│   ├── WinRM → cluster FQDN (domain field) → Get-ClusterNode → node names
│   └── DNS-resolve each node name → IP
│       (dnspython + cluster dns_servers; appends domain_name suffix if set)
│
├── Build FLAT task group:
│   ├── fetch_single_host.s(ip, cluster_id)   ← per node, all clusters
│   ├── ...
│   └── fetch_cluster_csv_storage.s(cluster_id, cluster_name, [ips])  ← per cluster
│
└── chord(flat_group)(aggregate_sync_results_with_csv.s())
    └── callback receives flat list → updates sync_metadata
```

### DNS Resolution

New helper `resolve_node_ip(node_name, dns_servers_str, domain_name)`:
1. Parse `dns_servers` comma-separated string → list of IPs
2. Configure `dnspython` Resolver with those nameservers
3. If `domain_name` set: try resolving `NODE1.domain_name` → A record
4. Fallback: try resolving short name `NODE1`
5. Last resort: return `node_name` as-is

### Chord Fix

Current (broken — nested groups):
```python
vm_sync_job = group(fetch_single_host.s(...) for node in nodes)
csv_scan_job = group(csv_tasks)
combined_job = group(vm_sync_job, csv_scan_job)  # nested!
chord(combined_job)(callback)
```

Fixed (flat group):
```python
all_tasks = [fetch_single_host.s(ip, cluster_id) for ...]
           + [fetch_cluster_csv_storage.s(...) for ...]
chord(group(all_tasks))(aggregate_sync_results_with_csv.s())
```

## Files Changed

| File | Changes |
|------|---------|
| `requirements.txt` | Add `dnspython>=2.4.0`, `cryptography>=41.0.0` |
| `app/utils/db.py` | ALTER TABLE for missing columns; fix column name mismatch |
| `app/routes/clusters.py` | Save all WinRM fields; fix SELECT queries; real connection test |
| `tasks/sync.py` | Fix `discover_cluster_nodes`; fix PS script; add `@shared_task`; flatten chord group |
| `tasks/csv_scanner.py` | Add `@shared_task`; fix `PS_GET_CSV_INFO` |
