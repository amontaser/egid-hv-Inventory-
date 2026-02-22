"""PowerShell scripts and collector functions for cluster storage."""

import logging
from typing import List, Dict
import winrm

from .winrm import run_ps_long

logger = logging.getLogger(__name__)

PS_GET_CSV_INFO = """
$ErrorActionPreference = 'SilentlyContinue'

$volumes = Get-ClusterSharedVolume -ErrorAction SilentlyContinue
if (-not $volumes) {
    @() | ConvertTo-Json
    return
}

$results = $volumes | ForEach-Object {
    $vol = $_
    $state = $vol | Get-ClusterSharedVolumeState -ErrorAction SilentlyContinue
    if (-not $state) { return }

    $partition = $state.SharedVolumeInfo.Partition
    $volPath   = $partition.Name

    $vhdCount = 0
    $vhdMaxGB = 0.0
    $vhdActGB = 0.0

    if ($volPath) {
        Get-ChildItem -Path $volPath -Recurse -Filter *.vhdx -ErrorAction SilentlyContinue | ForEach-Object {
            $vhd = Get-VHD -Path $_.FullName -ErrorAction SilentlyContinue
            if ($vhd) {
                $vhdCount++
                $vhdMaxGB += $vhd.Size / 1GB
                $vhdActGB += $vhd.FileSize / 1GB
            }
        }
    }

    $totalGB = [math]::Round($partition.Size / 1GB, 2)
    $freeGB  = [math]::Round($partition.FreeSpace / 1GB, 2)
    $usedGB  = [math]::Round(($partition.Size - $partition.FreeSpace) / 1GB, 2)
    $pct     = if ($partition.Size -gt 0) { [math]::Round(($usedGB / $totalGB) * 100, 2) } else { 0 }
    $overPct = if ($totalGB -gt 0) { [math]::Round(($vhdMaxGB / $totalGB) * 100, 2) } else { 0 }
    $overGB  = [math]::Round($vhdMaxGB - $totalGB, 2)

    [PSCustomObject]@{
        Name                  = $vol.Name
        VolumePath            = $volPath
        OwnerNode             = $vol.OwnerNode.Name
        State                 = $vol.State.ToString()
        TotalSizeGB           = $totalGB
        FreeSpaceGB           = $freeGB
        UsedSpaceGB           = $usedGB
        PercentUsed           = $pct
        MaintenanceMode       = if ($state.MaintenanceMode) { 1 } else { 0 }
        RedirectedAccess      = if ($state.RedirectedAccess) { 1 } else { 0 }
        VHDCount              = $vhdCount
        VHDMaxSizeGB          = [math]::Round($vhdMaxGB, 2)
        VHDActualSizeGB       = [math]::Round($vhdActGB, 2)
        OversubscriptionPercent = $overPct
        OversubscriptionGB    = $overGB
    }
}

if ($results) { $results | ConvertTo-Json -Depth 3 } else { @() | ConvertTo-Json }
"""


def collect_csv_volumes(session: winrm.Session) -> List[Dict]:
    """Collect Cluster Shared Volume info from a cluster node."""
    result = run_ps_long(session, PS_GET_CSV_INFO)
    return result or []
