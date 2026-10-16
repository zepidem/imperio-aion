# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
param([switch]$Once)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
while ($true) {
    try {
        $observedUtcMs = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
        $games = @(Get-Process -ErrorAction Stop | Where-Object { $_.ProcessName -in @('AION2','AION2-Win64-Shipping') })
        $ids = @($games | ForEach-Object { [int]$_.Id })
        $processStarts = @{}
        foreach ($gameProcess in $games) { $processStarts[[int]$gameProcess.Id] = $gameProcess.StartTime.ToUniversalTime().ToFileTimeUtc() }
        $connections = @()
        if ($ids.Count -gt 0) {
            $rows = @(Get-NetTCPConnection -State Established -ErrorAction Stop |
                Where-Object { [int]$_.OwningProcess -in $ids -and [int]$_.RemotePort -notin @(80,443) })
            $connections = @(foreach ($row in $rows) {
                $ownerId = [int]$row.OwningProcess
                $connectionStart = $null
                if ($row.CreationTime -is [datetime] -and $row.CreationTime.Year -ge 1970) {
                    $connectionStart = $row.CreationTime.ToUniversalTime().ToFileTimeUtc()
                }
                [ordered]@{ LocalAddress=[string]$row.LocalAddress; LocalPort=[int]$row.LocalPort;
                    RemoteAddress=[string]$row.RemoteAddress; RemotePort=[int]$row.RemotePort;
                    OwningProcess=$ownerId; ProcessStartedFiletime=$processStarts[$ownerId];
                    ConnectionStartedFiletime=$connectionStart }
            })
        }
        $result = [ordered]@{ ok=$true; observed_utc_ms=$observedUtcMs; game_count=$games.Count; connections=$connections }
    } catch {
        $result = [ordered]@{ ok=$false; error=$_.Exception.Message; connections=@() }
    }
    [Console]::WriteLine(($result | ConvertTo-Json -Depth 5 -Compress))
    if ($Once) { break }
    Start-Sleep -Seconds 1
}
