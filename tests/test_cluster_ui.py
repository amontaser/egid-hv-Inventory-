"""Clustered flag and per-node VM counts in the UI."""

import pytest
from sqlalchemy import text

from app import create_app
from app.models import User, db
from tasks.persistence.vms import save_vms
from werkzeug.security import generate_password_hash


@pytest.fixture
def app_client(monkeypatch):
    monkeypatch.setenv("APP_DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    app = create_app()
    app.config.update(TESTING=True)
    with app.app_context():
        db.session.execute(
            text(
                "INSERT INTO clusters (id, cluster_name, domain, is_enabled) "
                "VALUES (1, 'CLUSTER-01', 'cl.example.com', 1)"
            )
        )
        db.session.execute(
            text(
                "INSERT INTO cluster_nodes "
                "(cluster_name, node_name, node_state, ip_address) VALUES "
                "('CLUSTER-01', 'HV-Node01', 'Up', '192.168.60.221'), "
                "('CLUSTER-01', 'HV-Node02', 'Up', '192.168.60.222')"
            )
        )
        db.session.commit()

        save_vms(
            [
                {
                    "VMId": "vm-a",
                    "Name": "clustered-vm",
                    "State": "Running",
                    "IsClustered": True,
                    "ClusterRoleState": "Online",
                }
            ],
            "HV-NODE01",
            cluster_name="CLUSTER-01",
        )
        save_vms(
            [
                {
                    "VMId": "vm-b",
                    "Name": "standalone-vm",
                    "State": "Off",
                    "IsClustered": False,
                }
            ],
            "HV-NODE01",
            cluster_name="CLUSTER-01",
        )
        save_vms(
            [
                {
                    "VMId": "vm-c",
                    "Name": "other-node-vm",
                    "State": "Running",
                    "IsClustered": True,
                    "ClusterRoleState": "Online",
                }
            ],
            "HV-NODE02",
            cluster_name="CLUSTER-01",
        )
        save_vms(
            [
                {
                    "VMId": "vm-d",
                    "Name": "unknown-ha-vm",
                    "State": "Running",
                    "IsClustered": None,
                }
            ],
            "HV-NODE02",
            cluster_name="CLUSTER-01",
        )

        db.session.add(
            User(
                username="ui-tester",
                password_hash=generate_password_hash("ui-test-password"),
            )
        )
        db.session.commit()

        client = app.test_client()
        response = client.post(
            "/login",
            data={"username": "ui-tester", "password": "ui-test-password"},
        )
        assert response.status_code == 302
        yield client


def test_cluster_page_shows_per_node_vm_counts(app_client):
    response = app_client.get("/cluster/1")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert 'data-node="HV-Node01"' in html
    assert 'data-node="HV-Node02"' in html
    # HV-Node01: 1 clustered + 1 standalone
    assert 'data-total="2"' in html
    assert 'data-clustered="1"' in html
    assert 'data-standalone="1"' in html
    # HV-Node02: 1 clustered + 1 unknown
    assert 'data-unknown="1"' in html
    assert "Standalone" in html


def test_dashboard_marks_clustered_and_standalone_vms(app_client):
    response = app_client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert "Clustered" in html
    assert "Standalone" in html
