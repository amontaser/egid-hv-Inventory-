"""add unique constraints to child tables

Revision ID: 001_unique_constraints
Revises:
Create Date: 2026-05-12

"""

from alembic import op

revision = "001_unique_constraints"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint(
        "uq_vm_disks_vm_cluster_path",
        "vm_disks",
        ["vm_id", "cluster_name", "disk_path"],
    )
    op.create_unique_constraint(
        "uq_vm_networks_vm_cluster_adapter_mac",
        "vm_network_adapters",
        ["vm_id", "cluster_name", "adapter_name", "mac_address"],
    )
    op.create_unique_constraint(
        "uq_vm_snapshots_vm_cluster_name",
        "vm_snapshots",
        ["vm_id", "cluster_name", "snapshot_name"],
    )
    op.create_unique_constraint(
        "uq_vm_replication_vm_cluster", "vm_replication", ["vm_id", "cluster_name"]
    )


def downgrade():
    op.drop_constraint("uq_vm_replication_vm_cluster", "vm_replication", type_="unique")
    op.drop_constraint(
        "uq_vm_snapshots_vm_cluster_name", "vm_snapshots", type_="unique"
    )
    op.drop_constraint(
        "uq_vm_networks_vm_cluster_adapter_mac", "vm_network_adapters", type_="unique"
    )
    op.drop_constraint("uq_vm_disks_vm_cluster_path", "vm_disks", type_="unique")
