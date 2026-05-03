"""PowerShell scripts and collector functions for Hyper-V hosts."""

import logging
from typing import List, Dict, Optional
import winrm

from .winrm import run_ps

logger = logging.getLogger(__name__)

PS_GET_HOST_INFO = """
$ErrorActionPreference = "SilentlyContinue"
$clusterName = $null
try { $c = Get-Cluster -ErrorAction SilentlyContinue; if ($c) { $clusterName = $c.Name } } catch {}

$totalMemGB = 0
$freeMemGB = 0
$cpuSum = 0
$osCaption = "Unknown"
$osVersion = $null

try {
    $mem = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop
    $totalMemGB  = [math]::Round($mem.TotalVisibleMemorySize / 1MB, 2)
    $freeMemGB   = [math]::Round($mem.FreePhysicalMemory / 1MB, 2)
    $cpuSum = (Get-CimInstance -ClassName Win32_Processor -ErrorAction SilentlyContinue | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum
    $osCaption = $mem.Caption
    $osVersion = $mem.Version
} catch {
    try {
        $osInfo = Invoke-Command -ScriptBlock { [System.Environment]::OSVersion } -ErrorAction SilentlyContinue
        if ($osInfo) { $osCaption = "Windows"; $osVersion = $osInfo.Version.ToString() }
    } catch {}
    try {
        $cpuSum = (Get-VMHost -ErrorAction SilentlyContinue).LogicalProcessorCount
        if (-not $cpuSum) { $cpuSum = 0 }
    } catch {}
}

$vmCount = (Get-VM -ErrorAction SilentlyContinue).Count
if (-not $vmCount) { $vmCount = 0 }

$hvVersion = $null
$vhdPath = $null
$vmPath = $null
try {
    $hvHost = Get-VMHost -ErrorAction SilentlyContinue
    if ($hvHost) {
        $hvVersion = if ($hvHost.IntegrationServicesVersion) { $hvHost.IntegrationServicesVersion.ToString() } else { $null }
        $vhdPath = $hvHost.VirtualHardDiskPath
        $vmPath  = $hvHost.VirtualMachinePath
        if ($totalMemGB -eq 0) { $totalMemGB = [math]::Round((Get-VMHostNumaNode -ErrorAction SilentlyContinue | Measure-Object -Property MemoryAvailable -Sum).Sum / 1GB, 2) }
    }
} catch {}

[PSCustomObject]@{
    HostName           = $env:COMPUTERNAME
    ClusterName        = $clusterName
    TotalMemoryGB      = $totalMemGB
    AvailableMemoryGB  = $freeMemGB
    LogicalProcessors  = $cpuSum
    VMCount            = $vmCount
    OSVersion          = ($osCaption + " " + $osVersion)
    HyperVVersion      = $hvVersion
    VirtualHardDiskPath = $vhdPath
    VirtualMachinePath  = $vmPath
} | ConvertTo-Json -Depth 2
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
    """Returns single host info dict or None."""
    result = run_ps(session, PS_GET_HOST_INFO)
    if result and len(result) > 0:
        return result[0]
    return None


def collect_physical_disks(session: winrm.Session) -> List[Dict]:
    return run_ps(session, PS_GET_PHYSICAL_DISKS) or []


def collect_cluster_nodes(session: winrm.Session) -> List[Dict]:
    """Returns list of {Name, State, IPAddress} dicts."""
    return run_ps(session, PS_GET_CLUSTER_NODES) or []
