# Hyper-V Inventory Web Application

A Flask-based web application to inventory and monitor Microsoft Hyper-V environments. It collects data about virtual machines, hosts, storage (Cluster Shared Volumes), and more, using WinRM and PowerShell.

## Features

- **VM Inventory:** View detailed information about VMs including CPU, memory, disks, and network adapters.
- **Host Monitoring:** Track host performance and physical disk health.
- **Storage Management:** Monitor Cluster Shared Volumes (CSV) and VHD/VHDX oversubscription.
- **Client Management:** Associate VMs with clients and track VLAN usage.
- **Change Detection:** Automatically detect and notify on VM and storage changes.
- **Automated Sync:** Scheduled data collection via Celery and Redis.

## Tech Stack

- **Backend:** Flask, Flask-SQLAlchemy, Flask-Login
- **Task Queue:** Celery, Redis
- **Database:** PostgreSQL (production), SQLite (development)
- **Frontend:** Bootstrap 5 (via Flask-Bootstrap)
- **Collector:** WinRM, PowerShell

## Setup

1. **Clone the repository.**
2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
3. **Configure environment variables:**
   Create a `.env` file with the following variables:
   - `SECRET_KEY`: Flask secret key.
   - `DATABASE_URL`: Database connection string (e.g., `postgresql://user:password@localhost/dbname`).
   - `REDIS_URL`: Redis connection string.
   - `ADMIN_PASSWORD`: Default password for the 'admin' user.
   - `HYPERV_USERNAME`: Username for WinRM connections.
   - `HYPERV_PASSWORD`: Password for WinRM connections.
4. **Run the application:**
   ```bash
   python app.py
   ```
   Or using Docker:
   ```bash
   docker-compose up -d
   ```

## Testing

Run tests using pytest:
```bash
PYTHONPATH=. pytest
```

## License

MIT
