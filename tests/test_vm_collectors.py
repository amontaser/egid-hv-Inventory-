import json
import pytest
from unittest.mock import MagicMock, patch
from tasks.collectors.vms import collect_vms, collect_networks, PS_GET_VM_NETWORKS


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
