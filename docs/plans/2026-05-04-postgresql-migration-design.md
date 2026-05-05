# PostgreSQL Migration Design

## Summary

Replace SQLite with PostgreSQL using SQLAlchemy ORM, Flask-SQLAlchemy, Alembic migrations, and psycopg2. Fresh database — no data migration from SQLite.

## Architecture

### Database Layer (`app/db/`)

- **Flask-SQLAlchemy** for Flask integration with scoped sessions
- **SQLAlchemy 2.0** declarative models
- **Alembic** for schema migrations (replaces manual `app/db/migrations.py`)
- **psycopg2-binary** as the PostgreSQL driver
- PostgreSQL service added to `docker-compose.yml`

### Connection

- New env var: `DATABASE_URL` (e.g. `postgresql://user:pass@postgres:5432/hyperv_inventory`)
- Remove `DATABASE_PATH` env var
- Replace `get_db()` context manager with `db.session` scoped session
- Replace `get_db_connection()` with `db.session`

### Models (`app/models.py`)

Define all 20 tables as SQLAlchemy ORM models:
- `vm_info`, `vm_disks`, `vm_network_adapters`, `vm_snapshots`, `vm_replication`
- `hyperv_hosts`, `host_physical_disks`
- `sync_metadata`, `csv_scan_metadata`
- `cluster_shared_volumes`, `clusters`, `cluster_nodes`
- `clients`, `client_contacts`, `vm_clients`
- `vm_history`, `vm_notes`, `client_notes`
- `notifications`
- `account_managers`, `client_account_managers`
- `settings`

Type mapping: `INTEGER`→`Integer`, `TEXT`→`String/Text`, `REAL`→`Numeric`, `AUTOINCREMENT`→`Identity/autoincrement`.

### SQL Query Changes

- `?` positional placeholders → `:param` named bindings or ORM queries
- `INSERT OR IGNORE` → `INSERT ... ON CONFLICT DO NOTHING` or SQLAlchemy `insert().on_conflict_do_nothing()`
- `PRAGMA table_info()` → removed (Alembic handles migrations)
- `sqlite3.Row` dict access (`row["col"]`) → ORM attribute access (`row.col`)
- `executescript()` → individual DDL via Alembic migrations
- `sqlite_master` queries → removed

### Files to Modify

1. `app/db/__init__.py` — complete rewrite (SQLAlchemy engine + session factory)
2. `app/models.py` — rewrite as SQLAlchemy declarative models
3. `app/db/migrations.py` — replace with Alembic (new `alembic/` directory + `alembic.ini`)
4. `app/__init__.py` — initialize Flask-SQLAlchemy
5. `app/routes/*.py` (8 files) — swap raw SQL for ORM/session queries
6. `tasks/persistence/*.py` (3 files) — swap raw SQL for ORM queries
7. `tasks/orchestrator.py`, `tasks/monitor.py` — update DB calls
8. `tasks/collectors/winrm.py` — update DB call
9. `notifications/__init__.py` — update DB call
10. `docker-compose.yml` — add PostgreSQL service, update env vars
11. `requirements.txt` — add Flask-SQLAlchemy, psycopg2-binary, alembic
12. `Dockerfile` — no change needed (psycopg2-binary avoids libpq-dev)
13. Tests — update fixtures to use SQLAlchemy sessions

### Docker Compose Changes

Add PostgreSQL service:
```yaml
postgres:
  image: postgres:16-alpine
  container_name: hyperv-postgres
  environment:
    POSTGRES_DB: hyperv_inventory
    POSTGRES_USER: hyperv
    POSTGRES_PASSWORD: <password>
  volumes:
    - hyperv-pg-data:/var/lib/postgresql/data
  restart: unless-stopped
  networks:
    - hyperv-nw
```

Update all services to use `DATABASE_URL` instead of `DATABASE_PATH`.

### Authentication (Flask-Login)

Replace the current single-password auth with Flask-Login:
- New `users` table in PostgreSQL (username, password_hash, role, is_active)
- `User` model implementing Flask-Login's `UserMixin`
- `LoginManager` configured in `app/__init__.py`
- Replace 8 copy-pasted `login_required` decorators with Flask-Login's `@login_required`
- Login page uses username + password (bcrypt hashing via `werkzeug.security`)
- Admin seed user created on init
- User management UI in settings page

### New Dependencies

- `Flask-SQLAlchemy>=3.1`
- `SQLAlchemy>=2.0`
- `psycopg2-binary>=2.9`
- `alembic>=1.13`
- `Flask-Login>=0.6`

### Approach

**Phased conversion**: Replace the DB layer first (models + connection), then convert each route/task file one at a time. Each file conversion can be tested independently. Flask-Login integration happens alongside the route conversion.
