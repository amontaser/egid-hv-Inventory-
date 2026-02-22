import json
import pytest
from unittest.mock import MagicMock, patch
from tasks.collectors.winrm import run_ps, run_ps_long


def _make_session(stdout=b"", stderr=b"", status=0):
    session = MagicMock()
    result = MagicMock()
    result.status_code = status
    result.std_out = stdout
    result.std_err = stderr
    session.run_ps.return_value = result
    return session


def test_run_ps_returns_list_for_array():
    data = [{"Name": "VM1"}, {"Name": "VM2"}]
    session = _make_session(stdout=json.dumps(data).encode())
    result = run_ps(session, "Get-VM")
    assert result == data


def test_run_ps_wraps_single_dict_in_list():
    data = {"Name": "VM1"}
    session = _make_session(stdout=json.dumps(data).encode())
    result = run_ps(session, "Get-VM")
    assert result == [data]


def test_run_ps_returns_empty_list_for_null():
    session = _make_session(stdout=b"null")
    result = run_ps(session, "Get-VM")
    assert result == []


def test_run_ps_returns_none_on_error_status():
    session = _make_session(stderr=b"Access denied", status=1)
    result = run_ps(session, "Get-VM")
    assert result is None


def test_run_ps_returns_none_on_json_decode_error():
    session = _make_session(stdout=b"not json")
    result = run_ps(session, "Get-VM")
    assert result is None


def test_run_ps_propagates_os_error():
    session = MagicMock()
    session.run_ps.side_effect = OSError("connection refused")
    with pytest.raises(OSError):
        run_ps(session, "Get-VM")


def test_run_ps_long_delegates_small_scripts():
    data = [{"Name": "VM1"}]
    session = _make_session(stdout=json.dumps(data).encode())
    short_script = "Get-VM"  # well under 7000 bytes
    result = run_ps_long(session, short_script)
    assert result == data
    # Should call run_ps path (session.run_ps called once)
    assert session.run_ps.call_count == 1
