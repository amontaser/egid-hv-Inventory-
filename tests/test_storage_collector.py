import json
from unittest.mock import MagicMock
from tasks.collectors.storage import collect_csv_volumes, PS_GET_CSV_INFO


def _session_returning(data):
    session = MagicMock()
    result = MagicMock()
    result.status_code = 0
    result.std_out = json.dumps(data).encode()
    result.std_err = b""
    session.run_ps.return_value = result
    return session


def test_collect_csv_returns_list():
    csvs = [{"Name": "Cluster Disk 1", "TotalSizeGB": 1000, "FreeSpaceGB": 500}]
    session = _session_returning(csvs)
    result = collect_csv_volumes(session)
    assert len(result) == 1
    assert result[0]["Name"] == "Cluster Disk 1"


def test_collect_csv_returns_empty_list_on_error():
    session = MagicMock()
    r = MagicMock()
    r.status_code = 1
    r.std_err = b"not a cluster"
    r.std_out = b""
    session.run_ps.return_value = r
    result = collect_csv_volumes(session)
    assert result == []


def test_ps_get_csv_info_uses_correct_partition_path():
    # Verify the fixed property chain is used (not the old buggy VolumeName.Path)
    assert "VolumeName.Path" not in PS_GET_CSV_INFO
    assert "Partition.Name" in PS_GET_CSV_INFO or "$partition.Name" in PS_GET_CSV_INFO
    assert "Get-ClusterSharedVolumeState" in PS_GET_CSV_INFO
