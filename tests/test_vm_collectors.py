import json
import pytest
from unittest.mock import MagicMock, patch
from tasks.collectors.vms import (
    PS_GET_VMS,
    collect_vms,
    collect_networks,
    PS_GET_VM_NETWORKS,
)


def _session_returning(data):
    session = MagicMock()
    result = MagicMock()
    result.status_code = 0
    result.std_out = json.dumps(data).encode()
    result.std_err = b""
    session.run_ps.return_value = result
    return session


def test_collect_vms_returns_list():
    vms = [{"VMId": "abc", "Name": "VM1", "State": "Running"}]
    session = _session_returning(vms)
    result = collect_vms(session)
    assert len(result) == 1
    assert result[0]["Name"] == "VM1"


def test_collect_vms_returns_empty_list_on_none():
    session = MagicMock()
    result_mock = MagicMock()
    result_mock.status_code = 1
    result_mock.std_err = b"error"
    result_mock.std_out = b""
    session.run_ps.return_value = result_mock
    result = collect_vms(session)
    assert result == []


def test_collect_networks_includes_vlan():
    nets = [{"VMId": "abc", "AdapterName": "Network Adapter", "VlanId": 100}]
    session = _session_returning(nets)
    result = collect_networks(session)
    assert result[0]["VlanId"] == 100


def test_ps_get_vm_networks_script_contains_vlan():
    assert "Get-VMNetworkAdapterVlan" in PS_GET_VM_NETWORKS
    assert "AccessVlanId" in PS_GET_VM_NETWORKS


def test_ps_get_vms_script_detects_cluster_roles():
    """The VM script must report whether a VM is a cluster VM role.

    Get-ClusterGroup has no -GroupType parameter on every Hyper-V build, so
    the virtual-machine group type (111) must be filtered with Where-Object.
    """
    assert "Get-ClusterGroup" in PS_GET_VMS
    assert "GroupType -eq 111" in PS_GET_VMS
    assert "-GroupType 111" not in PS_GET_VMS
    assert "IsClustered" in PS_GET_VMS
    assert "ClusterRoleState" in PS_GET_VMS
    assert "ClusterOwnerNode" in PS_GET_VMS
    # An unresolved role lookup must stay unknown instead of reporting
    # every VM as standalone.
    assert "rolesResolved" in PS_GET_VMS


def test_collect_vms_returns_cluster_role_fields():
    vms = [
        {
            "VMId": "abc",
            "Name": "VM1",
            "State": "Running",
            "IsClustered": True,
            "ClusterRoleState": "Online",
            "ClusterOwnerNode": "HV-Node01",
        }
    ]
    session = _session_returning(vms)
    result = collect_vms(session)
    assert result[0]["IsClustered"] is True
    assert result[0]["ClusterRoleState"] == "Online"
    assert result[0]["ClusterOwnerNode"] == "HV-Node01"
