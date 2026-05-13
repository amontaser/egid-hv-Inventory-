"""PowerShell scripts and collector functions for VMs."""

import logging
from typing import List, Dict
import winrm

from .winrm import run_ps, run_ps_long

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PowerShell scripts
# ---------------------------------------------------------------------------

PS_GET_VMS = """
$ErrorActionPreference = "SilentlyContinue"
$clusterName = $null
try { $c = Get-Cluster -ErrorAction SilentlyContinue; if ($c) { $clusterName = $c.Name } } catch {}

Get-VM | ForEach-Object {
    $vm = $_
    $uptime = 0
    if ($vm.State -eq 'Running' -and $vm.Uptime) { $uptime = [int]$vm.Uptime.TotalSeconds }
    [PSCustomObject]@{
        VMId = $vm.Id
        Name = $vm.Name
        State = $vm.State.ToString()
        ComputerName = $vm.ComputerName
        CPUCount = $vm.ProcessorCount
        MemoryAssigned = [math]::Round($vm.MemoryAssigned / 1GB, 2)
        MemoryDemand = [math]::Round($vm.MemoryDemand / 1GB, 2)
        MemoryStartup = [math]::Round($vm.MemoryStartup / 1GB, 2)
        MemoryMinimum = [math]::Round($vm.MemoryMinimum / 1GB, 2)
        MemoryMaximum = [math]::Round($vm.MemoryMaximum / 1GB, 2)
        DynamicMemory = $vm.DynamicMemoryEnabled
        Generation = $vm.Generation
        Version = $vm.Version
        IntegrationServicesVersion = if ($vm.IntegrationServicesVersion) { $vm.IntegrationServicesVersion.ToString() } else { $null }
        VirtualHardDiskPath = if ($vm.VirtualHardDisks.Count -gt 0) { $vm.VirtualHardDisks[0].Path } else { $null }
        VirtualMachinePath = $vm.ConfigurationLocation
        PrimaryIPAddress = ($vm.NetworkAdapters | Where-Object {$_.IPAddresses} | ForEach-Object {$_.IPAddresses} | Where-Object {$_ -match '^\\d+\\.\\d+\\.\\d+\\.\\d+$'} | Select-Object -First 1)
        ClusterName = $clusterName
        CreatedTime = if ($vm.CreationTime) { $vm.CreationTime.ToString("yyyy-MM-dd HH:mm:ss") } else { $null }
        UptimeSeconds = $uptime
    }
} | ConvertTo-Json -Depth 3 -Compress
"""

PS_GET_VM_DISKS = """
Get-VM | ForEach-Object {
    $vm = $_
    Get-VMHardDiskDrive -VM $vm | ForEach-Object {
        $d = $_
        $fmt = "VHDX"
        if ($d.Path -match ".vhd$") { $fmt = "VHD" }
        @{VMId=$vm.Id;DiskName=$d.Name;DiskPath=$d.Path;DiskFormat=$fmt;ControllerType=$d.ControllerType.ToString();ControllerNumber=$d.ControllerNumber;ControllerLocation=$d.ControllerLocation}
    }
} | ConvertTo-Json
"""

PS_GET_VM_DISK_SIZES = """
Get-VM | ForEach-Object {
    $vm = $_
    Get-VMHardDiskDrive -VM $vm | ForEach-Object {
        $d = $_
        $sizeGB = 0; $usedGB = 0
        try { $vhd = Get-VHD -Path $d.Path -ErrorAction Stop; $sizeGB = [math]::Round($vhd.Size / 1GB, 2); $usedGB = [math]::Round($vhd.FileSize / 1GB, 2) } catch {}
        @{VMId=$vm.Id;DiskPath=$d.Path;Size=$sizeGB;UsedGB=$usedGB}
    }
} | ConvertTo-Json
"""

PS_GET_VM_NETWORKS = """
Get-VM | ForEach-Object {
    $vm = $_
    $vm.NetworkAdapters | ForEach-Object {
        $a = $_
        $vlanId = 0
        try { $v = Get-VMNetworkAdapterVlan -VMNetworkAdapter $a -ErrorAction SilentlyContinue; if ($v -and $v.OperationMode -eq 'Access') { $vlanId = $v.AccessVlanId } } catch {}
        @{VMId=$vm.Id;AdapterName=$a.Name;SwitchName=$a.SwitchName;MacAddress=$a.MacAddress;IPAddresses=($a.IPAddresses | Where-Object { $_ -match '^\\d+\\.\\d+\\.\\d+\\.\\d+$' }) -join ',';IsConnected=$a.Status -eq 'Ok';VlanId=$vlanId}
    }
} | ConvertTo-Json
"""

PS_GET_VM_SNAPSHOTS = """
Get-VM | ForEach-Object {
    $vm = $_
    Get-VMSnapshot -VMName $vm.Name -ErrorAction SilentlyContinue | ForEach-Object {
        $parent = $null
        if ($_.ParentSnapshot) { $parent = $_.ParentSnapshot.Name }
        @{VMId=$vm.Id;SnapshotName=$_.Name;SnapshotType=$_.SnapshotType.ToString();CreationTime=$_.CreationTime.ToString("yyyy-MM-dd HH:mm:ss");ParentSnapshotName=$parent}
    }
} | ConvertTo-Json
"""

PS_GET_VM_REPLICATION = """
Get-VM | ForEach-Object {
    $vm = $_
    $rep = $null
    try { $rep = $vm | Get-VMReplication -ErrorAction Stop } catch {}
    if ($rep) {
        $freq = $null
        if ($rep.ReplicationFrequency) { $freq = $rep.ReplicationFrequency * 60 }
        $lrt = $null
        if ($rep.LastReplicationTime) { $lrt = $rep.LastReplicationTime.ToString("yyyy-MM-dd HH:mm:ss") }
        @{VMId=$vm.Id;ReplicationState=$rep.State.ToString();ReplicationHealth=$rep.Health.ToString();ReplicationMode=$rep.ReplicationMode.ToString();PrimaryServer=$rep.PrimaryServer;ReplicaServer=$rep.ReplicaServer;FrequencySeconds=$freq;LastReplicationTime=$lrt}
    }
} | ConvertTo-Json
"""


# ---------------------------------------------------------------------------
# Collector functions
# ---------------------------------------------------------------------------


def collect_vms(session: winrm.Session) -> List[Dict]:
    result = run_ps_long(session, PS_GET_VMS, context="collect_vms", force_encoded=True)
    if result:
        hosts = set(v.get("ComputerName", "?") for v in result)
        logger.info(
            f"collect_vms got {len(result)} VMs from PowerShell, hosts: {hosts}"
        )
    return result or []


def collect_disks(session: winrm.Session) -> List[Dict]:
    disks = run_ps(session, PS_GET_VM_DISKS, context="collect_disks")
    if not disks:
        logger.info("Collected 0 VM disks")
        return []
    sizes = run_ps(session, PS_GET_VM_DISK_SIZES, context="collect_disk_sizes")
    if sizes:
        size_map = {
            (s.get("VMId"), s.get("DiskPath")): (s.get("Size", 0), s.get("UsedGB", 0))
            for s in sizes
        }
        for d in disks:
            key = (d.get("VMId"), d.get("DiskPath"))
            sz, used = size_map.get(key, (0, 0))
            if sz or used:
                d["Size"] = sz
                d["UsedGB"] = used
    logger.info(f"Collected {len(disks)} VM disks")
    return disks


def collect_networks(session: winrm.Session) -> List[Dict]:
    result = run_ps(session, PS_GET_VM_NETWORKS, context="collect_networks")
    return result or []


def collect_snapshots(session: winrm.Session) -> List[Dict]:
    result = run_ps(session, PS_GET_VM_SNAPSHOTS, context="collect_snapshots")
    return result or []


def collect_replication(session: winrm.Session) -> List[Dict]:
    result = run_ps(session, PS_GET_VM_REPLICATION, context="collect_replication")
    return result or []
