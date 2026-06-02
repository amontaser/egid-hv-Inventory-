# VM Tags Feature Design

## Overview

Proxmox-style tagging for VMs — auto-colored pill badges, multiple tags per VM, filterable across all views.

## Data Model

Two new tables:

```sql
tags (
  id SERIAL PRIMARY KEY,
  name VARCHAR(100) UNIQUE NOT NULL,
  color VARCHAR(7) NOT NULL  -- hex color, auto-derived from name hash
)

vm_tags (
  vm_id VARCHAR(255) NOT NULL,
  cluster_name VARCHAR(255) NOT NULL,
  tag_id INTEGER REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY (vm_id, cluster_name, tag_id),
  FOREIGN KEY (vm_id, cluster_name) REFERENCES vm_info(vm_id, cluster_name) ON DELETE CASCADE
)
```

Color derivation: deterministic hash of tag name → hue value → hex color. Same name always same color.

## Seed Tags

Pre-created on DB init: Production, Test, Pre-Production, Development, Internal, Staging, DR, Decommission.

## Tag Management (Settings Page)

New section in Settings: list tags with color swatch + VM count, create/rename/delete.

## VM Views

- **Dashboard (index.html)**: Tags as colored badges per VM row. New tag filter dropdown. Included in search.
- **Client details**: Tags visible on each VM row.
- **VM detail page**: Tags at top with inline editor (type to search/add, click X to remove).
- **Export**: Tags column added to all VM exports (comma-separated).

## Adding/Removing Tags

- VM detail page: inline picker with search, click to add, X to remove.
- Client details VM list: same inline picker.
- Tags survive syncs (vm_tags is NOT cleared by _clear_host_child_data).

## UI Style

- Small pill badges with auto-color background + white text
- Proxmox-like: `[Production]` `[Internal]` next to VM name
