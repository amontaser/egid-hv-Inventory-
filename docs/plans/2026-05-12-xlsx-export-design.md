# XLSX Export Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add Excel (XLSX) export to all major views, replacing the existing CSV export with filter-aware XLSX downloads.

**Architecture:** Create a shared `app/utils/export.py` helper that builds styled `openpyxl` workbooks. Add export routes to each existing blueprint (vms, hosts, storage, clients, settings). The VM details export produces a multi-sheet workbook. All exports respect the current page filters (cluster_id, search, change_type, etc.) passed as query params. Replace the existing `/export/csv` route with `/export/vms.xlsx`.

**Tech Stack:** openpyxl, Flask, SQLAlchemy

---

### Task 1: Add openpyxl dependency

**Files:**
- Modify: `requirements.txt`

**Step 1: Add openpyxl to requirements.txt**

Append to the end of `requirements.txt`:
```
openpyxl>=3.1.0
```

**Step 2: Commit**

```bash
git add requirements.txt
git commit -m "chore: add openpyxl dependency for XLSX export"
```

---

### Task 2: Create shared XLSX export utility

**Files:**
- Create: `app/utils/export.py`

**Step 1: Create `app/utils/export.py`**

```python
from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from flask import make_response
from datetime import datetime


HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
HEADER_ALIGNMENT = Alignment(horizontal="center", vertical="center")


def make_sheet(ws, headers, rows, col_widths=None):
    ws.append(headers)
    for cell in ws[1]:
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = HEADER_ALIGNMENT
    for row in rows:
        ws.append(row)
    ws.freeze_panes = "A2"
    if col_widths:
        for i, w in enumerate(col_widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
    else:
        for col in ws.columns:
            max_length = 0
            for cell in col:
                val = str(cell.value) if cell.value else ""
                max_length = max(max_length, len(val))
            ws.column_dimensions[get_column_letter(col[0].column)].width = min(
                max_length + 2, 50
            )


def build_workbook(sheets_data):
    wb = Workbook()
    first = True
    for sheet_name, headers, rows, col_widths in sheets_data:
        if first:
            ws = wb.active
            ws.title = sheet_name[:31]
            first = False
        else:
            ws = wb.create_sheet(title=sheet_name[:31])
        make_sheet(ws, headers, rows, col_widths)
    return wb


def workbook_response(wb, filename_prefix):
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    resp = make_response(buf.getvalue())
    resp.headers["Content-Type"] = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    resp.headers["Content-Disposition"] = (
        f"attachment; filename={filename_prefix}_{timestamp}.xlsx"
    )
    return resp
```

**Step 2: Commit**

```bash
git add app/utils/export.py
git commit -m "feat: add shared XLSX export utility"
```

---

### Task 3: Replace CSV export with VMs XLSX export (respecting filters)

**Files:**
- Modify: `app/routes/storage.py` (replace `/export/csv` route with `/export/vms.xlsx`)

**Step 1: Add imports at top of storage.py**

Add to existing imports:
```python
from app.utils.export import build_workbook, workbook_response
```

Remove the `csv` and `StringIO` imports (no longer needed).

**Step 2: Replace the `export_csv` function (lines 217-305)**

Replace the entire `export_csv` function with:

```python
@bp.route("/export/vms.xlsx")
@login_required
def export_vms_xlsx():
    cluster_id = request.args.get("cluster_id")
    search = request.args.get("search", "")
    cluster_name = None
    if cluster_id and cluster_id.isdigit():
        with get_db() as db:
            row = db.execute(
                text("SELECT cluster_name FROM clusters WHERE id = :cid"),
                {"cid": int(cluster_id)},
            ).fetchone()
            if row:
                cluster_name = row._mapping["cluster_name"]

    params = {}
    where = []
    if cluster_name:
        where.append("v.cluster_name = :cn")
        params["cn"] = cluster_name
    if search:
        where.append("v.machine_name LIKE :search")
        params["search"] = f"%{search}%"
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    with get_db() as db:
        vms_raw = db.execute(
            text(f"""
            SELECT
                v.machine_name, v.vm_id, v.host_name, v.cluster_name, v.state,
                v.cpu_count, v.memory_assigned_gb, v.memory_demand_gb,
                v.dynamic_memory_enabled, v.generation, v.version, v.notes,
                (SELECT ROUND(SUM(size_gb), 2) FROM vm_disks WHERE vm_id = v.vm_id AND cluster_name = v.cluster_name) as total_disk_gb,
                (SELECT COUNT(*) FROM vm_disks WHERE vm_id = v.vm_id AND cluster_name = v.cluster_name) as disk_count,
                (SELECT {str_agg("ip_addresses")} FROM vm_network_adapters WHERE vm_id = v.vm_id AND cluster_name = v.cluster_name) as ip_addresses,
                (SELECT COUNT(*) FROM vm_snapshots WHERE vm_id = v.vm_id AND cluster_name = v.cluster_name) as snapshot_count,
                v.created_at, v.updated_at
            FROM vm_info v
            {where_sql}
            ORDER BY v.machine_name
        """),
            params,
        ).fetchall()

    headers = [
        "Machine Name", "VM ID", "Host", "Cluster", "State", "vCPUs",
        "Memory (GB)", "Memory Demand (GB)", "Dynamic Memory", "Generation",
        "Version", "Notes", "Total Disk (GB)", "Disk Count", "IP Addresses",
        "Snapshots", "Created", "Updated",
    ]
    rows = []
    for r in vms_raw:
        d = _row_to_dict(r)
        rows.append([
            d["machine_name"], d["vm_id"], d["host_name"], d["cluster_name"],
            d["state"], d["cpu_count"], d["memory_assigned_gb"],
            d["memory_demand_gb"], "Yes" if d["dynamic_memory_enabled"] else "No",
            d["generation"], d["version"], d["notes"], d["total_disk_gb"],
            d["disk_count"], d["ip_addresses"], d["snapshot_count"],
            str(d["created_at"]) if d.get("created_at") else "",
            str(d["updated_at"]) if d.get("updated_at") else "",
        ])

    wb = build_workbook([("VMs", headers, rows)])
    return workbook_response(wb, "HyperV_Inventory")
```

**Step 3: Keep the old `/export/csv` as a redirect for backwards compatibility**

Add below the new route:

```python
@bp.route("/export/csv")
@login_required
def export_csv():
    args = {}
    cid = request.args.get("cluster_id")
    if cid:
        args["cluster_id"] = cid
    s = request.args.get("search")
    if s:
        args["search"] = s
    return redirect(url_for("storage.export_vms_xlsx", **args))
```

Add `url_for` to the Flask import at the top.

**Step 4: Commit**

```bash
git add app/routes/storage.py
git commit -m "feat: replace CSV export with filter-aware XLSX export"
```

---

### Task 4: Add VM details multi-sheet XLSX export

**Files:**
- Modify: `app/routes/vms.py` (add `/export/vm/<vm_id>.xlsx` route)

**Step 1: Add import at top of vms.py**

```python
from app.utils.export import build_workbook, workbook_response
```

**Step 2: Add the export route**

Add after the `vm_details` function (after line 413):

```python
@bp.route("/export/vm/<vm_id>.xlsx")
@login_required
def export_vm_xlsx(vm_id):
    cluster_name = request.args.get("cluster")
    with get_db() as db:
        if cluster_name:
            vm = db.execute(
                text("SELECT * FROM vm_info WHERE vm_id = :vm_id AND cluster_name = :cn"),
                {"vm_id": vm_id, "cn": cluster_name},
            ).fetchone()
        else:
            vm = db.execute(
                text("SELECT * FROM vm_info WHERE vm_id = :vm_id"),
                {"vm_id": vm_id},
            ).fetchone()
        if not vm:
            abort(404)
        vm_d = _row_to_dict(vm)
        cn = vm_d["cluster_name"]

        disks = [_row_to_dict(r) for r in db.execute(
            text("SELECT * FROM vm_disks WHERE vm_id = :vm_id AND cluster_name = :cn"),
            {"vm_id": vm_id, "cn": cn},
        ).fetchall()]
        networks = [_row_to_dict(r) for r in db.execute(
            text("SELECT * FROM vm_network_adapters WHERE vm_id = :vm_id AND cluster_name = :cn"),
            {"vm_id": vm_id, "cn": cn},
        ).fetchall()]
        snapshots = [_row_to_dict(r) for r in db.execute(
            text("SELECT * FROM vm_snapshots WHERE vm_id = :vm_id AND cluster_name = :cn"),
            {"vm_id": vm_id, "cn": cn},
        ).fetchall()]
        rep_row = db.execute(
            text("SELECT * FROM vm_replication WHERE vm_id = :vm_id AND cluster_name = :cn"),
            {"vm_id": vm_id, "cn": cn},
        ).fetchone()
        replication = _row_to_dict(rep_row)

    sheets = []

    info_headers = ["Field", "Value"]
    info_rows = [
        ["Machine Name", vm_d.get("machine_name")],
        ["VM ID", vm_d.get("vm_id")],
        ["Host", vm_d.get("host_name")],
        ["Cluster", vm_d.get("cluster_name")],
        ["State", vm_d.get("state")],
        ["vCPUs", vm_d.get("cpu_count")],
        ["Memory Assigned (GB)", vm_d.get("memory_assigned_gb")],
        ["Memory Demand (GB)", vm_d.get("memory_demand_gb")],
        ["Dynamic Memory", "Yes" if vm_d.get("dynamic_memory_enabled") else "No"],
        ["Generation", vm_d.get("generation")],
        ["Version", vm_d.get("version")],
        ["Notes", vm_d.get("notes") or ""],
    ]
    sheets.append(("Info", info_headers, info_rows, [25, 40]))

    if disks:
        disk_headers = ["VM Name", "Controller", "Type", "Path", "Size (GB)", "VHD Type", "Format"]
        disk_rows = [[
            d.get("vm_name"), d.get("controller_type"), d.get("disk_type"),
            d.get("path"), d.get("size_gb"), d.get("vhd_type"), d.get("vhd_format"),
        ] for d in disks]
        sheets.append(("Disks", disk_headers, disk_rows))

    if networks:
        net_headers = ["Adapter", "Switch", "VLAN", "IP Addresses", "MAC", "Connected"]
        net_rows = [[
            n.get("adapter_name"), n.get("switch_name"), n.get("vlan_id"),
            n.get("ip_addresses"), n.get("mac_address"),
            "Yes" if n.get("connected") else "No",
        ] for n in networks]
        sheets.append(("Networks", net_headers, net_rows))

    if snapshots:
        snap_headers = ["Name", "Creation Time", "Size (GB)"]
        snap_rows = [[
            s.get("snapshot_name"), str(s.get("creation_time", "")),
            s.get("size_gb"),
        ] for s in snapshots]
        sheets.append(("Snapshots", snap_headers, snap_rows))

    if replication:
        rep_headers = ["Field", "Value"]
        rep_rows = [
            ["State", replication.get("replication_state")],
            ["Mode", replication.get("replication_mode")],
            ["Frequency (sec)", replication.get("replication_frequency_sec")],
            ["Last Sync", str(replication.get("last_sync_time", ""))],
            ["Primary Server", replication.get("primary_server")],
        ]
        sheets.append(("Replication", rep_headers, rep_rows, [25, 40]))

    wb = build_workbook(sheets)
    return workbook_response(wb, f"VM_{vm_d.get('machine_name', vm_id)}")
```

**Step 3: Commit**

```bash
git add app/routes/vms.py
git commit -m "feat: add VM details multi-sheet XLSX export"
```

---

### Task 5: Add Hosts XLSX export

**Files:**
- Modify: `app/routes/hosts.py` (add `/export/hosts.xlsx` route)

**Step 1: Add import at top of hosts.py**

```python
from app.utils.export import build_workbook, workbook_response
```

**Step 2: Add the export route at the end of the file**

```python
@bp.route("/export/hosts.xlsx")
@login_required
def export_hosts_xlsx():
    cluster_id = request.args.get("cluster_id")
    params = {}
    cluster_filter = ""
    if cluster_id and cluster_id.isdigit():
        with get_db() as db:
            row = db.execute(
                text("SELECT cluster_name FROM clusters WHERE id = :cid"),
                {"cid": int(cluster_id)},
            ).fetchone()
            if row:
                params["cn"] = row._mapping["cluster_name"]
                cluster_filter = "WHERE v.cluster_name = :cn"

    with get_db() as db:
        hosts_raw = db.execute(
            text(f"""
            SELECT
                COALESCE(hh.host_name, v.host_name) as display_name,
                v.cluster_name,
                hh.total_memory_gb,
                hh.available_memory_gb,
                hh.logical_processors,
                hh.os_version,
                v.actual_vm_count
            FROM (
                SELECT host_name, cluster_name, COUNT(*) as actual_vm_count
                FROM vm_info
                {cluster_filter}
                GROUP BY host_name, cluster_name
            ) v
            LEFT JOIN hyperv_hosts hh ON v.host_name = hh.host_name
            ORDER BY v.cluster_name, COALESCE(hh.host_name, v.host_name)
        """),
            params,
        ).fetchall()

    headers = ["Host Name", "Cluster", "Total Memory (GB)", "Available Memory (GB)", "CPUs", "OS", "VM Count"]
    rows = []
    for h in hosts_raw:
        d = _row_to_dict(h)
        rows.append([
            d["display_name"], d["cluster_name"], d["total_memory_gb"],
            d["available_memory_gb"], d["logical_processors"],
            d["os_version"], d["actual_vm_count"],
        ])

    wb = build_workbook([("Hosts", headers, rows)])
    return workbook_response(wb, "HyperV_Hosts")
```

**Step 3: Commit**

```bash
git add app/routes/hosts.py
git commit -m "feat: add hosts XLSX export with cluster filter"
```

---

### Task 6: Add Storage XLSX export

**Files:**
- Modify: `app/routes/storage.py` (add `/export/storage.xlsx` route)

**Step 1: Add the export route at the end of the file**

```python
@bp.route("/export/storage.xlsx")
@login_required
def export_storage_xlsx():
    cluster_id = request.args.get("cluster_id")
    params = {}
    where = ""
    if cluster_id and cluster_id.isdigit():
        with get_db() as db:
            row = db.execute(
                text("SELECT cluster_name FROM clusters WHERE id = :cid"),
                {"cid": int(cluster_id)},
            ).fetchone()
            if row:
                params["cn"] = row._mapping["cluster_name"]
                where = "WHERE cluster_name = :cn"

    with get_db() as db:
        storage_raw = db.execute(
            text(f"""
            SELECT * FROM cluster_shared_volumes
            {where}
            ORDER BY cluster_name, name
        """),
            params,
        ).fetchall()

    headers = [
        "Volume Name", "Cluster", "Path", "Owner Node",
        "Total (GB)", "Used (GB)", "Free (GB)", "% Used",
        "VHD Count", "VHD Max (GB)", "VHD Actual (GB)", "% Oversubscribed",
    ]
    rows = []
    for s in storage_raw:
        d = _row_to_dict(s)
        pct = round((d["used_space_gb"] / d["total_size_gb"]) * 100, 1) if d.get("total_size_gb") else 0
        over = round((d["vhd_max_size_gb"] / d["total_size_gb"]) * 100, 1) if d.get("total_size_gb") else 0
        rows.append([
            d.get("name"), d.get("cluster_name"), d.get("path"),
            d.get("owner_node"), d.get("total_size_gb"), d.get("used_space_gb"),
            d.get("free_space_gb"), pct, d.get("vhd_count"),
            d.get("vhd_max_size_gb"), d.get("vhd_actual_size_gb"), over,
        ])

    wb = build_workbook([("Storage", headers, rows)])
    return workbook_response(wb, "HyperV_Storage")
```

**Step 2: Commit**

```bash
git add app/routes/storage.py
git commit -m "feat: add storage XLSX export with cluster filter"
```

---

### Task 7: Add Clients XLSX export

**Files:**
- Modify: `app/routes/clients.py` (add `/export/clients.xlsx` route)

**Step 1: Add import at top of clients.py**

```python
from app.utils.export import build_workbook, workbook_response
```

**Step 2: Add the export route at the end of the file**

```python
@bp.route("/export/clients.xlsx")
@login_required
def export_clients_xlsx():
    with get_db() as db:
        clients_raw = db.execute(
            text(f"""
            SELECT
                c.id, c.name, c.website, c.country, c.description,
                (SELECT COUNT(*) FROM vm_clients WHERE client_id = c.id) as vm_count,
                (SELECT {str_agg("vm_name")} FROM (
                    SELECT v.machine_name as vm_name
                    FROM vm_clients vc
                    JOIN vm_info v ON vc.vm_id = v.vm_id AND vc.cluster_name = v.cluster_name
                    WHERE vc.client_id = c.id
                    ORDER BY v.machine_name
                )) as vm_names
            FROM clients c
            ORDER BY c.name
        """)
        ).fetchall()

    headers = ["ID", "Name", "Website", "Country", "Description", "VM Count", "VMs"]
    rows = [[
        d["id"], d["name"], d.get("website", ""), d.get("country", ""),
        d.get("description", ""), d["vm_count"], d.get("vm_names", ""),
    ] for d in (_row_to_dict(c) for c in clients_raw)]

    wb = build_workbook([("Clients", headers, rows)])
    return workbook_response(wb, "HyperV_Clients")
```

**Step 3: Commit**

```bash
git add app/routes/clients.py
git commit -m "feat: add clients XLSX export"
```

---

### Task 8: Add History XLSX export

**Files:**
- Modify: `app/routes/settings.py` (add `/export/history.xlsx` route)

**Step 1: Add import at top of settings.py**

```python
from app.utils.export import build_workbook, workbook_response
```

**Step 2: Add the export route at the end of the file**

```python
@bp.route("/export/history.xlsx")
@login_required
def export_history_xlsx():
    search = request.args.get("search", "")
    change_type = request.args.get("change_type", "")
    date_from = request.args.get("date_from", "")

    where_clauses = ["1=1"]
    params = {}
    if search:
        where_clauses.append("h.machine_name LIKE :search")
        params["search"] = f"%{search}%"
    if change_type:
        where_clauses.append("h.change_type = :change_type")
        params["change_type"] = change_type
    if date_from:
        where_clauses.append("DATE(h.detected_at) >= :date_from")
        params["date_from"] = date_from
    where_sql = " AND ".join(where_clauses)

    with get_db() as db:
        history_raw = db.execute(
            text(f"""
            SELECT h.*,
                   (SELECT machine_name FROM vm_info WHERE vm_id = h.vm_id LIMIT 1) as machine_name
            FROM vm_history h
            WHERE {where_sql}
            ORDER BY h.detected_at DESC
        """),
            params,
        ).fetchall()

    headers = ["Machine Name", "Change Type", "Old Value", "New Value", "Detected At"]
    rows = [[
        h_dict.get("machine_name", ""), h_dict.get("change_type", ""),
        h_dict.get("old_value", ""), h_dict.get("new_value", ""),
        str(h_dict.get("detected_at", "")),
    ] for h_dict in (_row_to_dict(h) for h in history_raw)]

    wb = build_workbook([("History", headers, rows)])
    return workbook_response(wb, "HyperV_History")
```

**Step 3: Commit**

```bash
git add app/routes/settings.py
git commit -m "feat: add history XLSX export with filters"
```

---

### Task 9: Update all template export buttons

**Files:**
- Modify: `templates/base.html` (sidebar nav link)
- Modify: `templates/index.html` (export button)
- Modify: `templates/vms.html` (export button)
- Modify: `templates/hosts.html` (add export button)
- Modify: `templates/storage.html` (add export button)
- Modify: `templates/clients.html` (add export button)
- Modify: `templates/global_history.html` (add export button)
- Modify: `templates/vm_details.html` (add export button)

**Step 1: Update `base.html` sidebar nav**

Change the existing export link from `/export/csv` to `/export/vms.xlsx`:

Old: `href="/export/csv"`
New: `href="/export/vms.xlsx"`

And change the label from "Export Data" to "Export VMs".

**Step 2: Update `index.html` export button**

Change from:
```html
<a href="/export/csv" class="btn btn-outline-secondary"><i class="bi bi-download"></i> Export</a>
```
To:
```html
<a href="/export/vms.xlsx" class="btn btn-outline-secondary"><i class="bi bi-file-earmark-spreadsheet"></i> Export</a>
```

**Step 3: Update `vms.html` export button**

Change from:
```html
<a href="/export/csv" class="btn btn-outline-secondary btn-sm">
    <i class="bi bi-download me-1"></i>Export
</a>
```
To:
```html
<a href="/export/vms.xlsx" class="btn btn-outline-secondary btn-sm">
    <i class="bi bi-file-earmark-spreadsheet me-1"></i>Export
</a>
```

**Step 4: Add export button to `hosts.html`**

In the right-side `<div class="d-flex gap-2">`, add as the first child before the Expand All button:
```html
<a href="/export/hosts.xlsx" class="btn btn-outline-secondary btn-sm">
    <i class="bi bi-file-earmark-spreadsheet me-1"></i>Export
</a>
```

If the `d-flex gap-2` div is conditional (`{% if clusters_with_hosts %}`), move the export button outside the conditional or make the div unconditional.

**Step 5: Add export button to `storage.html`**

In the right-side `<div class="d-flex gap-2">`, add as the first child before the Rescan CSV button:
```html
<a href="/export/storage.xlsx" class="btn btn-outline-secondary">
    <i class="bi bi-file-earmark-spreadsheet me-1"></i>Export
</a>
```

**Step 6: Add export button to `clients.html`**

Change the right side from a single button to a flex group:
```html
<div class="d-flex gap-2">
    <a href="/export/clients.xlsx" class="btn btn-outline-secondary">
        <i class="bi bi-file-earmark-spreadsheet me-1"></i>Export
    </a>
    <a href="/client/add" class="btn btn-primary">
        <i class="bi bi-plus-circle me-1"></i> Add New Client
    </a>
</div>
```

**Step 7: Add export button to `global_history.html`**

Change the right side:
```html
<div class="d-flex gap-2">
    <a href="/export/history.xlsx" class="btn btn-outline-secondary">
        <i class="bi bi-file-earmark-spreadsheet me-1"></i>Export
    </a>
    <a href="/" class="btn btn-outline-secondary">
        <i class="bi bi-house me-1"></i> Dashboard
    </a>
</div>
```

**Step 8: Add export button to `vm_details.html`**

In the right-side `<div class="d-flex gap-2">`, add as the first child:
```html
<a href="/export/vm/{{ vm.vm_id }}.xlsx{% if vm.cluster_name %}?cluster={{ vm.cluster_name }}{% endif %}" class="btn btn-outline-secondary btn-sm">
    <i class="bi bi-file-earmark-spreadsheet me-1"></i>Export
</a>
```

**Step 9: Commit**

```bash
git add templates/
git commit -m "feat: update all pages with XLSX export buttons"
```

---

### Task 10: Install openpyxl in Docker and rebuild

**Step 1: Rebuild Docker image**

```bash
docker compose build web celery-worker
docker compose up -d web celery-worker
```

**Step 2: Verify exports work**

Test each export URL in the browser:
- `/export/vms.xlsx`
- `/export/hosts.xlsx`
- `/export/storage.xlsx`
- `/export/clients.xlsx`
- `/export/history.xlsx`
- `/export/vm/<some-vm-id>.xlsx`
