"""PowerShell scripts and collector functions for Hyper-V hosts."""

import logging
from typing import List, Dict, Optional
import winrm

from .winrm import run_ps, run_ps_long

logger = logging.getLogger(__name__)

PS_GET_HOST_MEMORY = """
$ErrorActionPreference = "SilentlyContinue"
$m = Get-CimInstance Win32_OperatingSystem
@{HostName = $env:COMPUTERNAME; TotalMemoryGB = [math]::Round($m.TotalVisibleMemorySize / 1MB, 2); AvailableMemoryGB = [math]::Round($m.FreePhysicalMemory / 1MB, 2); OSVersion = ($m.Caption + " " + $m.Version)} | ConvertTo-Json
"""

PS_GET_HOST_CPU = """
$ErrorActionPreference = "SilentlyContinue"
$c = (Get-CimInstance Win32_Processor | Measure-Object NumberOfLogicalProcessors -Sum).Sum
$v = (Get-VM).Count
@{LogicalProcessors = $c; VMCount = $v} | ConvertTo-Json
"""

PS_GET_HOST_HYPERV = """
$ErrorActionPreference = "SilentlyContinue"
$clusterName = $null
try { $c = Get-Cluster; if ($c) { $clusterName = $c.Name } } catch {}
$h = Get-VMHost
$hvVer = if ($h.IntegrationServicesVersion) { $h.IntegrationServicesVersion.ToString() } else { $null }
@{ClusterName = $clusterName; HyperVVersion = $hvVer; VirtualHardDiskPath = $h.VirtualHardDiskPath; VirtualMachinePath = $h.VirtualMachinePath} | ConvertTo-Json
"""

PS_GET_PHYSICAL_DISKS = """
$ErrorActionPreference = "SilentlyContinue"
$disks = Get-Disk -ErrorAction SilentlyContinue
Get-PhysicalDisk -ErrorAction SilentlyContinue | ForEach-Object {
    $phys = $_
    $disk = $disks | Where-Object { $_.SerialNumber -eq $phys.SerialNumber } | Select-Object -First 1
    [PSCustomObject]@{
        FriendlyName       = $phys.FriendlyName
        SerialNumber       = $phys.SerialNumber
        MediaType          = $phys.MediaType.ToString()
        SizeGB             = [math]::Round($phys.Size / 1GB, 2)
        HealthStatus       = $phys.HealthStatus.ToString()
        OperationalStatus  = ($phys.OperationalStatus | Select-Object -First 1).ToString()
        BusType            = $phys.BusType.ToString()
        PartitionStyle     = if ($disk) { $disk.PartitionStyle.ToString() } else { 'Unknown' }
        DiskNumber         = if ($disk) { $disk.DiskNumber } else { $null }
    }
} | ConvertTo-Json -Depth 3
"""

PS_GET_CLUSTER_NODES = """
$ErrorActionPreference = "SilentlyContinue"
try {
    Get-ClusterNode -ErrorAction Stop | ForEach-Object {
        $nodeName = $_.Name
        $ip = $null
        try {
            $addr = [System.Net.Dns]::GetHostAddresses($nodeName) |
                Where-Object {
                    $_.AddressFamily -eq 'InterNetwork' -and
                    -not $_.ToString().StartsWith('169.254.') -and
                    -not $_.ToString().StartsWith('127.')
                } | Select-Object -First 1
            if ($addr) { $ip = $addr.IPAddressToString }
        } catch {}
        if (-not $ip) {
            try {
                $iface = Get-ClusterNetworkInterface -Node $nodeName -ErrorAction SilentlyContinue |
                    Where-Object { $_.State -eq 'Up' -and $_.Address -match '^\\d+\\.\\d+\\.\\d+\\.\\d+$' } |
                    Select-Object -First 1
                if ($iface) { $ip = $iface.Address }
            } catch {}
        }
        [PSCustomObject]@{ Name = $nodeName; State = $_.State.ToString(); IPAddress = $ip }
    } | ConvertTo-Json -Depth 3
} catch {
    @() | ConvertTo-Json
}
"""


def collect_host_info(session: winrm.Session) -> Optional[Dict]:
    """Returns single host info dict or None. Uses multiple small PS calls
    to stay under WinRM input size limits on hosts with restricted configs."""
    info = {}
    for script in [PS_GET_HOST_MEMORY, PS_GET_HOST_CPU, PS_GET_HOST_HYPERV]:
        result = run_ps(session, script)
        if result and len(result) > 0 and isinstance(result[0], dict):
            info.update(result[0])
    return info if info else None


def collect_physical_disks(session: winrm.Session) -> List[Dict]:
    return run_ps(session, PS_GET_PHYSICAL_DISKS) or []


def collect_cluster_nodes(session: winrm.Session) -> List[Dict]:
    """Returns list of {Name, State, IPAddress} dicts."""
    return run_ps(session, PS_GET_CLUSTER_NODES) or []
