"""PowerShell scripts and collector functions for cluster storage."""

import logging
from typing import List, Dict
import winrm

from .winrm import run_ps_long

logger = logging.getLogger(__name__)

PS_GET_CSV_INFO = """
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'SilentlyContinue'

$result = @()

try {
    $csvs = Get-ClusterSharedVolume -ErrorAction Stop

    # Process CSVs in parallel using ForEach-Object -Parallel (PowerShell 7+)
    # Fallback to sequential processing if parallel not available
    if ($PSVersionTable.PSVersion.Major -ge 7) {
        $result = $csvs | ForEach-Object -Parallel {
            $csv = $_
            $i = $csv.SharedVolumeInfo[0]

            # Get VHD information for this CSV
            $vhdFiles = @()

            try {
                # Get all VHD/VHDX files on this CSV
                $volumePath = $i.FriendlyVolumeName
                if (Test-Path $volumePath) {
                    # Use .NET enumeration for better performance (10x faster than Get-ChildItem)
                    $vhdFiles = [System.IO.Directory]::EnumerateFiles($volumePath, '*.vhd', 'AllTopLevelDirectories') +
                                [System.IO.Directory]::EnumerateFiles($volumePath, '*.vhdx', 'AllTopLevelDirectories')

                    # Filter out system directories
                    $excludePaths = @('Recovery', 'System Volume Information', '$Recycle.Bin', 'Config')
                    $vhdFiles = $vhdFiles | Where-Object {
                        $file = $_
                        -not ($excludePaths | Where-Object { $file -like "*\$_*" })
                    }
                }
            } catch {
                # Ignore errors accessing VHD files
            }

            # Calculate VHD statistics
            $vhdCount = $vhdFiles.Count
            $vhdMaxSizeGB = 0
            $vhdActualSizeGB = 0

            # Batch process VHD details (limit concurrent Get-VHD calls)
            $batchSize = 10
            for ($idx = 0; $idx -lt $vhdFiles.Count; $idx += $batchSize) {
                $batch = $vhdFiles[$idx..[Math]::Min($idx + $batchSize - 1, $vhdFiles.Count - 1)]

                foreach ($vhdPath in $batch) {
                    try {
                        $vhdDetails = Get-VHD -Path $vhdPath -ErrorAction SilentlyContinue
                        if ($vhdDetails) {
                            $vhdMaxSizeGB += [math]::Round($vhdDetails.Size / 1GB, 2)
                            $vhdActualSizeGB += [math]::Round($vhdDetails.FileSize / 1GB, 2)
                        }
                    } catch {
                        # If we can't get VHD details, estimate from file size
                        try {
                            $fileSize = (Get-Item $vhdPath -ErrorAction SilentlyContinue).Length
                            $vhdMaxSizeGB += [math]::Round($fileSize / 1GB, 2)
                            $vhdActualSizeGB += [math]::Round($fileSize / 1GB, 2)
                        } catch {
                            # Skip files we can't access
                        }
                    }
                }
            }

            # Calculate oversubscription
            $partitionSizeGB = [math]::Round($i.Partition.Size / 1GB, 2)
            $oversubscriptionPercent = if ($partitionSizeGB -gt 0) {
                [math]::Round(($vhdMaxSizeGB / $partitionSizeGB) * 100, 1)
            } else { 0 }

            [PSCustomObject]@{
                Name = $csv.Name
                VolumePath = $i.FriendlyVolumeName
                OwnerNode = if ($csv.OwnerNode) { $csv.OwnerNode.Name } else { $null }
                State = $csv.State.ToString()
                TotalSizeGB = $partitionSizeGB
                FreeSpaceGB = [math]::Round($i.Partition.FreeSpace / 1GB, 2)
                UsedSpaceGB = [math]::Round(($i.Partition.Size - $i.Partition.FreeSpace) / 1GB, 2)
                PercentUsed = if ($partitionSizeGB -gt 0) {
                    [math]::Round((($i.Partition.Size - $i.Partition.FreeSpace) / $i.Partition.Size) * 100, 1)
                } else { 0 }
                MaintenanceMode = $csv.MaintenanceMode
                RedirectedAccess = $csv.RedirectedAccess
                VHDCount = $vhdCount
                VHDMaxSizeGB = $vhdMaxSizeGB
                VHDActualSizeGB = $vhdActualSizeGB
                OversubscriptionPercent = $oversubscriptionPercent
                OversubscriptionGB = if ($vhdMaxSizeGB -gt $partitionSizeGB) {
                    [math]::Round($vhdMaxSizeGB - $partitionSizeGB, 2)
                } else { 0 }
            }
        } -ThrottleLimit 4
    } else {
        # Fallback for PowerShell 5.x (sequential but optimized)
        foreach ($csv in $csvs) {
            $i = $csv.SharedVolumeInfo[0]

            # Get VHD information for this CSV
            $vhdFiles = @()

            try {
                # Get all VHD/VHDX files on this CSV
                $volumePath = $i.FriendlyVolumeName
                if (Test-Path $volumePath) {
                    # Use Get-ChildItem with optimized parameters
                    $vhdFiles = Get-ChildItem -Path $volumePath -Recurse -Include *.vhd,*.vhdx -ErrorAction SilentlyContinue -Depth 3 |
                        Where-Object {
                            $_.DirectoryName -notmatch '(Recovery|System Volume Information|\$Recycle\.Bin|Config)'
                        }
                }
            } catch {
                # Ignore errors accessing VHD files
            }

            # Calculate VHD statistics
            $vhdCount = $vhdFiles.Count
            $vhdMaxSizeGB = 0
            $vhdActualSizeGB = 0

            foreach ($vhd in $vhdFiles) {
                try {
                    $vhdDetails = Get-VHD -Path $vhd.FullName -ErrorAction SilentlyContinue
                    if ($vhdDetails) {
                        $vhdMaxSizeGB += [math]::Round($vhdDetails.Size / 1GB, 2)
                        $vhdActualSizeGB += [math]::Round($vhdDetails.FileSize / 1GB, 2)
                    }
                } catch {
                    # If we can't get VHD details, use file size
                    $vhdMaxSizeGB += [math]::Round($vhd.Length / 1GB, 2)
                    $vhdActualSizeGB += [math]::Round($vhd.Length / 1GB, 2)
                }
            }

            # Calculate oversubscription
            $partitionSizeGB = [math]::Round($i.Partition.Size / 1GB, 2)
            $oversubscriptionPercent = if ($partitionSizeGB -gt 0) {
                [math]::Round(($vhdMaxSizeGB / $partitionSizeGB) * 100, 1)
            } else { 0 }

            $result += [PSCustomObject]@{
                Name = $csv.Name
                VolumePath = $i.FriendlyVolumeName
                OwnerNode = if ($csv.OwnerNode) { $csv.OwnerNode.Name } else { $null }
                State = $csv.State.ToString()
                TotalSizeGB = $partitionSizeGB
                FreeSpaceGB = [math]::Round($i.Partition.FreeSpace / 1GB, 2)
                UsedSpaceGB = [math]::Round(($i.Partition.Size - $i.Partition.FreeSpace) / 1GB, 2)
                PercentUsed = if ($partitionSizeGB -gt 0) {
                    [math]::Round((($i.Partition.Size - $i.Partition.FreeSpace) / $i.Partition.Size) * 100, 1)
                } else { 0 }
                MaintenanceMode = $csv.MaintenanceMode
                RedirectedAccess = $csv.RedirectedAccess
                VHDCount = $vhdCount
                VHDMaxSizeGB = $vhdMaxSizeGB
                VHDActualSizeGB = $vhdActualSizeGB
                OversubscriptionPercent = $oversubscriptionPercent
                OversubscriptionGB = if ($vhdMaxSizeGB -gt $partitionSizeGB) {
                    [math]::Round($vhdMaxSizeGB - $partitionSizeGB, 2)
                } else { 0 }
            }
        }
    }
} catch {
    throw "Failed to get Cluster Shared Volumes: $($_.Exception.Message)"
}

if ($result.Count -eq 0) {
    throw "No Cluster Shared Volumes found"
}

$result | ConvertTo-Json -Depth 3
"""


def collect_csv_volumes(session: winrm.Session) -> List[Dict]:
    """Collect Cluster Shared Volume info from a cluster node."""
    result = run_ps_long(session, PS_GET_CSV_INFO, context="collect_csv_volumes")
    logger.info(f"Collected {len(result or [])} CSV volumes")
    return result or []
