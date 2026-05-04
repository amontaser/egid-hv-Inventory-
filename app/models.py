from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin

db = SQLAlchemy()


class VMInfo(db.Model):
    __tablename__ = "vm_info"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    machine_name = db.Column(db.String(255), nullable=False)
    vm_id = db.Column(db.String(255), nullable=False)
    cluster_name = db.Column(db.String(255), nullable=False)
    host_name = db.Column(db.String(255))
    state = db.Column(db.String(50))
    uptime_seconds = db.Column(db.Integer)
    cpu_count = db.Column(db.Integer)
    memory_assigned_gb = db.Column(db.Numeric(10, 2))
    memory_demand_gb = db.Column(db.Numeric(10, 2))
    memory_startup_gb = db.Column(db.Numeric(10, 2))
    memory_minimum_gb = db.Column(db.Numeric(10, 2))
    memory_maximum_gb = db.Column(db.Numeric(10, 2))
    dynamic_memory_enabled = db.Column(db.Boolean)
    generation = db.Column(db.Integer)
    version = db.Column(db.String(50))
    notes = db.Column(db.Text)
    created_at = db.Column(db.String(50))
    updated_at = db.Column(db.String(50))

    __table_args__ = (
        db.UniqueConstraint("vm_id", "cluster_name"),
        db.Index("idx_vm_info_machine_name", "machine_name"),
        db.Index("idx_vm_info_host", "host_name"),
        db.Index("idx_vm_info_state", "state"),
        db.Index("idx_vm_info_cluster", "cluster_name"),
    )


class VMDisk(db.Model):
    __tablename__ = "vm_disks"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    vm_id = db.Column(db.String(255), nullable=False)
    cluster_name = db.Column(db.String(255), nullable=False)
    disk_name = db.Column(db.String(255))
    disk_path = db.Column(db.Text)
    disk_format = db.Column(db.String(50))
    size_gb = db.Column(db.Numeric(10, 2))
    used_gb = db.Column(db.Numeric(10, 2))
    controller_type = db.Column(db.String(100))
    controller_number = db.Column(db.Integer)
    controller_location = db.Column(db.Integer)

    __table_args__ = (
        db.ForeignKeyConstraint(
            ["vm_id", "cluster_name"],
            ["vm_info.vm_id", "vm_info.cluster_name"],
        ),
        db.Index("idx_vm_disks_vm_id", "vm_id", "cluster_name"),
    )


class VMNetworkAdapter(db.Model):
    __tablename__ = "vm_network_adapters"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    vm_id = db.Column(db.String(255), nullable=False)
    cluster_name = db.Column(db.String(255), nullable=False)
    adapter_name = db.Column(db.String(255))
    switch_name = db.Column(db.String(255))
    vlan_id = db.Column(db.Integer)
    mac_address = db.Column(db.String(17))
    ip_addresses = db.Column(db.Text)
    is_connected = db.Column(db.Boolean)
    bandwidth_setting = db.Column(db.String(100))

    __table_args__ = (
        db.ForeignKeyConstraint(
            ["vm_id", "cluster_name"],
            ["vm_info.vm_id", "vm_info.cluster_name"],
        ),
        db.Index("idx_vm_network_vm_id", "vm_id", "cluster_name"),
    )


class VMSnapshot(db.Model):
    __tablename__ = "vm_snapshots"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    vm_id = db.Column(db.String(255), nullable=False)
    cluster_name = db.Column(db.String(255), nullable=False)
    snapshot_name = db.Column(db.String(255))
    snapshot_id = db.Column(db.String(255))
    snapshot_type = db.Column(db.String(50))
    creation_time = db.Column(db.String(50))
    parent_snapshot_id = db.Column(db.String(255))

    __table_args__ = (
        db.ForeignKeyConstraint(
            ["vm_id", "cluster_name"],
            ["vm_info.vm_id", "vm_info.cluster_name"],
        ),
        db.Index("idx_vm_snapshots_vm_id", "vm_id", "cluster_name"),
    )


class VMReplication(db.Model):
    __tablename__ = "vm_replication"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    vm_id = db.Column(db.String(255), nullable=False)
    cluster_name = db.Column(db.String(255), nullable=False)
    replication_state = db.Column(db.String(50))
    replication_health = db.Column(db.String(50))
    replication_mode = db.Column(db.String(50))
    primary_server = db.Column(db.String(255))
    replica_server = db.Column(db.String(255))
    frequency_seconds = db.Column(db.Integer)
    last_replication_time = db.Column(db.String(50))

    __table_args__ = (
        db.ForeignKeyConstraint(
            ["vm_id", "cluster_name"],
            ["vm_info.vm_id", "vm_info.cluster_name"],
        ),
    )


class HyperVHost(db.Model):
    __tablename__ = "hyperv_hosts"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    host_name = db.Column(db.String(255), unique=True)
    cluster_name = db.Column(db.String(255))
    total_memory_gb = db.Column(db.Numeric(10, 2))
    available_memory_gb = db.Column(db.Numeric(10, 2))
    logical_processors = db.Column(db.Integer)
    vm_count = db.Column(db.Integer)
    os_version = db.Column(db.String(255))
    hyperv_version = db.Column(db.String(50))
    vhd_default_path = db.Column(db.Text)
    vm_default_path = db.Column(db.Text)
    connection_ip = db.Column(db.String(50))
    last_updated = db.Column(db.String(50))


class HostPhysicalDisk(db.Model):
    __tablename__ = "host_physical_disks"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    host_name = db.Column(db.String(255), nullable=False)
    friendly_name = db.Column(db.String(255))
    serial_number = db.Column(db.String(255))
    media_type = db.Column(db.String(50))
    size_gb = db.Column(db.Numeric(10, 2))
    health_status = db.Column(db.String(50))
    operational_status = db.Column(db.String(50))
    bus_type = db.Column(db.String(50))
    partition_style = db.Column(db.String(50))
    disk_number = db.Column(db.Integer)
    last_updated = db.Column(db.String(50))

    __table_args__ = (
        db.UniqueConstraint("host_name", "serial_number"),
        db.Index("idx_host_disks_host", "host_name"),
    )


class SyncMetadata(db.Model):
    __tablename__ = "sync_metadata"

    id = db.Column(db.Integer, primary_key=True)
    last_sync_start = db.Column(db.String(50))
    last_sync_end = db.Column(db.String(50))
    last_sync_status = db.Column(db.String(50))
    vms_synced = db.Column(db.Integer)
    hosts_discovered = db.Column(db.Integer)
    hosts_processed = db.Column(db.Integer)
    current_host = db.Column(db.String(255))
    current_cluster = db.Column(db.String(255))
    errors = db.Column(db.Text)


class CSVScanMetadata(db.Model):
    __tablename__ = "csv_scan_metadata"

    id = db.Column(db.Integer, primary_key=True)
    cluster_id = db.Column(db.Integer)
    last_scan_start = db.Column(db.String(50))
    last_scan_end = db.Column(db.String(50))
    scan_status = db.Column(db.String(50))
    volumes_scanned = db.Column(db.Integer)
    total_vhds_found = db.Column(db.Integer)
    scan_duration_seconds = db.Column(db.Float)
    errors = db.Column(db.Text)


class ClusterSharedVolume(db.Model):
    __tablename__ = "cluster_shared_volumes"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(255), nullable=False)
    volume_path = db.Column(db.Text)
    owner_node = db.Column(db.String(255))
    state = db.Column(db.String(50))
    total_size_gb = db.Column(db.Numeric(10, 2))
    free_space_gb = db.Column(db.Numeric(10, 2))
    used_space_gb = db.Column(db.Numeric(10, 2))
    percent_used = db.Column(db.Numeric(10, 2))
    maintenance_mode = db.Column(db.Boolean, default=False)
    redirected_access = db.Column(db.Boolean, default=False)
    vhd_count = db.Column(db.Integer, default=0)
    vhd_max_size_gb = db.Column(db.Numeric(10, 2), default=0)
    vhd_actual_size_gb = db.Column(db.Numeric(10, 2), default=0)
    oversubscription_percent = db.Column(db.Numeric(10, 2), default=0)
    oversubscription_gb = db.Column(db.Numeric(10, 2), default=0)
    cluster_name = db.Column(db.String(255))
    last_updated = db.Column(db.String(50))

    __table_args__ = (db.UniqueConstraint("name", "volume_path", "cluster_name"),)


class Cluster(db.Model):
    __tablename__ = "clusters"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    cluster_name = db.Column(db.String(255), nullable=False, unique=True)
    location = db.Column(db.String(255))
    is_enabled = db.Column(db.Boolean, default=True)
    domain = db.Column(db.String(255))
    cluster_name_for_ps = db.Column(db.String(255))
    domain_name = db.Column(db.String(255))
    dns_servers = db.Column(db.Text)
    username = db.Column(db.String(255))
    password = db.Column(db.String(255))
    transport = db.Column(db.String(50), default="ntlm")
    require_https = db.Column(db.Boolean, default=False)


class ClusterNode(db.Model):
    __tablename__ = "cluster_nodes"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    cluster_name = db.Column(db.String(255), nullable=False)
    node_name = db.Column(db.String(255), nullable=False)
    node_state = db.Column(db.String(50))
    ip_address = db.Column(db.String(50))
    last_updated = db.Column(db.String(50))

    __table_args__ = (db.UniqueConstraint("cluster_name", "node_name"),)


class Client(db.Model):
    __tablename__ = "clients"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(255), nullable=False)
    website = db.Column(db.String(500))
    country = db.Column(db.String(100))
    description = db.Column(db.Text)
    state = db.Column(db.Boolean, default=True)


class ClientContact(db.Model):
    __tablename__ = "client_contacts"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255))
    phone = db.Column(db.String(50))
    is_primary_contact = db.Column(db.Boolean, default=False)
    job_title = db.Column(db.String(255))
    mobile_phone = db.Column(db.String(50))


class VMClient(db.Model):
    __tablename__ = "vm_clients"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    vm_id = db.Column(db.String(255), nullable=False)
    cluster_name = db.Column(db.String(255), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)

    __table_args__ = (
        db.ForeignKeyConstraint(
            ["vm_id", "cluster_name"],
            ["vm_info.vm_id", "vm_info.cluster_name"],
        ),
    )


class VMHistory(db.Model):
    __tablename__ = "vm_history"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    vm_id = db.Column(db.String(255), nullable=False)
    cluster_name = db.Column(db.String(255))
    machine_name = db.Column(db.String(255), nullable=False)
    change_type = db.Column(db.String(50), nullable=False)
    field_name = db.Column(db.String(255))
    old_value = db.Column(db.Text)
    new_value = db.Column(db.Text)
    change_description = db.Column(db.Text)
    detected_at = db.Column(db.String(50), nullable=False)

    __table_args__ = (
        db.Index("idx_vm_history_vm_id", "vm_id"),
        db.Index("idx_vm_history_date", "detected_at"),
        db.Index("idx_vm_history_type", "change_type"),
    )


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    notification_type = db.Column(db.String(50), nullable=False)
    vm_id = db.Column(db.String(255))
    machine_name = db.Column(db.String(255), nullable=False)
    message = db.Column(db.Text, nullable=False)
    severity = db.Column(db.String(50), default="info")
    cluster_name = db.Column(db.String(255))
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.String(50), nullable=False)

    __table_args__ = (
        db.Index("idx_notifications_read", "is_read"),
        db.Index("idx_notifications_date", "created_at"),
    )


class AccountManager(db.Model):
    __tablename__ = "account_managers"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255))
    phone = db.Column(db.String(50))
    state = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.String(50))
    updated_at = db.Column(db.String(50))


class ClientAccountManager(db.Model):
    __tablename__ = "client_account_managers"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    client_id = db.Column(
        db.Integer, db.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    manager_id = db.Column(
        db.Integer,
        db.ForeignKey("account_managers.id", ondelete="CASCADE"),
        nullable=False,
    )
    assigned_at = db.Column(db.String(50))

    __table_args__ = (db.UniqueConstraint("client_id", "manager_id"),)


class VMNote(db.Model):
    __tablename__ = "vm_notes"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    vm_id = db.Column(db.String(255), nullable=False)
    cluster_name = db.Column(db.String(255), nullable=False)
    note_text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.String(50))
    updated_at = db.Column(db.String(50))

    __table_args__ = (
        db.ForeignKeyConstraint(
            ["vm_id", "cluster_name"],
            ["vm_info.vm_id", "vm_info.cluster_name"],
            ondelete="CASCADE",
        ),
    )


class ClientNote(db.Model):
    __tablename__ = "client_notes"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    client_id = db.Column(
        db.Integer, db.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    note_text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.String(50))
    updated_at = db.Column(db.String(50))


class Setting(db.Model):
    __tablename__ = "settings"

    key = db.Column(db.String(255), primary_key=True)
    value = db.Column(db.Text)
    updated_at = db.Column(db.String(50))


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), default="user")
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.String(50))
    updated_at = db.Column(db.String(50))
