"""PowerShell scripts and collector functions for cluster storage."""

import logging
from typing import List, Dict
import winrm

from .winrm import run_ps_long

logger = logging.getLogger(__name__)

PS_GET_CSV_INFO = """
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'

try {
    $csvs = Get-ClusterSharedVolume -ErrorAction Stop
    $result = @()

    foreach ($csv in $csvs) {
        $i = $csv.SharedVolumeInfo[0]
        $volPath = $i.FriendlyVolumeName
        
        # Get VHD files (simplified - skip system dirs)
        $vhdFiles = @()
        if ($volPath -and (Test-Path $volPath)) {
            try {
                $vhdFiles = Get-ChildItem $volPath -Recurse -Include *.vhd,*.vhdx -ErrorAction SilentlyContinue -Depth 2 |
                    Where-Object { $_.DirectoryName -notmatch 'Recovery|System Volume Information|\$Recycle\.Bin|Config' }
            } catch { }
        }

        # Calculate VHD stats
        $vhdCount = $vhdFiles.Count
        $vhdMaxGB = 0
        $vhdActualGB = 0

        foreach ($vhd in $vhdFiles) {
            try {
                $details = Get-VHD -Path $vhd.FullName -ErrorAction SilentlyContinue
                if ($details) {
                    $vhdMaxGB += [math]::Round($details.Size / 1GB, 2)
                    $vhdActualGB += [math]::Round($details.FileSize / 1GB, 2)
                }
            } catch {
                $vhdMaxGB += [math]::Round($vhd.Length / 1GB, 2)
                $vhdActualGB += [math]::Round($vhd.Length / 1GB, 2)
            }
        }

        $totalGB = [math]::Round($i.Partition.Size / 1GB, 2)
        $freeGB = [math]::Round($i.Partition.FreeSpace / 1GB, 2)
        $usedGB = [math]::Round(($i.Partition.Size - $i.Partition.FreeSpace) / 1GB, 2)
        $pctUsed = if ($totalGB -gt 0) { [math]::Round(($usedGB / $totalGB) * 100, 1) } else { 0 }
        $oversubPct = if ($totalGB -gt 0) { [math]::Round(($vhdMaxGB / $totalGB) * 100, 1) } else { 0 }

        $result += [PSCustomObject]@{
            Name = $csv.Name
            VolumePath = $volPath
            OwnerNode = if ($csv.OwnerNode) { $csv.OwnerNode.Name } else { $null }
            State = $csv.State.ToString()
            TotalSizeGB = $totalGB
            FreeSpaceGB = $freeGB
            UsedSpaceGB = $usedGB
            PercentUsed = $pctUsed
            MaintenanceMode = $csv.MaintenanceMode
            RedirectedAccess = $csv.RedirectedAccess
            VHDCount = $vhdCount
            VHDMaxSizeGB = $vhdMaxGB
            VHDActualSizeGB = $vhdActualGB
            OversubscriptionPercent = $oversubPct
            OversubscriptionGB = if ($vhdMaxGB -gt $totalGB) { [math]::Round($vhdMaxGB - $totalGB, 2) } else { 0 }
        }
    }

    if ($result.Count -eq 0) {
        throw "No Cluster Shared Volumes found"
    }

    $result | ConvertTo-Json -Depth 3
} catch {
    throw "Failed to get Cluster Shared Volumes: $($_.Exception.Message)"
}
"""


def collect_csv_volumes(session: winrm.Session) -> List[Dict]:
    """Collect Cluster Shared Volume info from a cluster node."""
    result = run_ps_long(session, PS_GET_CSV_INFO, context="collect_csv_volumes")
    logger.info(f"Collected {len(result or [])} CSV volumes")
    return result or []
