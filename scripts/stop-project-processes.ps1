[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,

    [string]$Ports = "8000,5173",

    [int]$TimeoutSeconds = 15
)

$ErrorActionPreference = "SilentlyContinue"

$resolvedRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path.TrimEnd("\")
$portList = @(
    $Ports -split "," |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ -match "^\d+$" } |
        ForEach-Object { [int]$_ }
)

if ($portList.Count -eq 0) {
    Write-Host "No ports were supplied."
    exit 0
}

function Get-ListeningProcessIds {
    param([int[]]$TargetPorts)

    $ids = @()
    $connections = Get-NetTCPConnection -State Listen -LocalPort $TargetPorts -ErrorAction SilentlyContinue
    if ($connections) {
        $ids += $connections | Select-Object -ExpandProperty OwningProcess
    } else {
        $portSet = @{}
        foreach ($port in $TargetPorts) {
            $portSet[$port] = $true
        }
        foreach ($line in netstat -ano -p TCP 2>$null) {
            if ($line -notmatch "\bLISTENING\b") {
                continue
            }
            $parts = $line -split "\s+" | Where-Object { $_ }
            if ($parts.Count -lt 5) {
                continue
            }
            $localAddress = $parts[1]
            $pidText = $parts[-1]
            if ($localAddress -match ":(\d+)$" -and $pidText -match "^\d+$") {
                $port = [int]$Matches[1]
                if ($portSet.ContainsKey($port)) {
                    $ids += [int]$pidText
                }
            }
        }
    }
    $ids | Where-Object { $_ -and $_ -gt 0 } | Sort-Object -Unique
}

function Add-Target {
    param(
        [System.Collections.Generic.HashSet[int]]$Set,
        [int]$ProcessId
    )

    if ($ProcessId -le 0 -or $ProcessId -eq $PID) {
        return
    }
    [void]$Set.Add($ProcessId)
}

function Add-Descendants {
    param(
        [System.Collections.Generic.HashSet[int]]$Set,
        [object[]]$Processes,
        [int]$ProcessId
    )

    foreach ($child in $Processes | Where-Object { $_.ParentProcessId -eq $ProcessId }) {
        Add-Target -Set $Set -ProcessId ([int]$child.ProcessId)
        Add-Descendants -Set $Set -Processes $Processes -ProcessId ([int]$child.ProcessId)
    }
}

function Add-ProjectAncestor {
    param(
        [System.Collections.Generic.HashSet[int]]$Set,
        [hashtable]$ById,
        [int]$ProcessId
    )

    $current = $ById[$ProcessId]
    while ($current -and $current.ParentProcessId) {
        $parent = $ById[[int]$current.ParentProcessId]
        if (-not $parent) {
            break
        }

        $name = [string]$parent.Name
        $cmd = [string]$parent.CommandLine
        $isProjectAncestor =
            ($name -ieq "python.exe" -and $cmd -match "uvicorn\s+backend\.main:app") -or
            ($name -ieq "cmd.exe" -and $cmd -match "npm\s+run\s+dev") -or
            ($name -ieq "cmd.exe" -and $cmd -match "\bvite\b") -or
            ($name -ieq "node.exe" -and $cmd -match "\bvite\b")

        if (-not $isProjectAncestor) {
            break
        }

        Add-Target -Set $Set -ProcessId ([int]$parent.ProcessId)
        $current = $parent
    }
}

$processes = @(Get-CimInstance Win32_Process)
$byId = @{}
foreach ($process in $processes) {
    $byId[[int]$process.ProcessId] = $process
}

$targetIds = [System.Collections.Generic.HashSet[int]]::new()
$listeningIds = @(Get-ListeningProcessIds -TargetPorts $portList)

foreach ($id in $listeningIds) {
    Add-Target -Set $targetIds -ProcessId ([int]$id)
    Add-Descendants -Set $targetIds -Processes $processes -ProcessId ([int]$id)
    Add-ProjectAncestor -Set $targetIds -ById $byId -ProcessId ([int]$id)
}

$portPattern = ($portList | ForEach-Object { [regex]::Escape([string]$_) }) -join "|"
foreach ($process in $processes) {
    $name = [string]$process.Name
    $cmd = [string]$process.CommandLine
    if (-not $cmd) {
        continue
    }

    $matchesProjectServer =
        ($name -ieq "python.exe" -and $cmd -match "uvicorn\s+backend\.main:app") -or
        ($name -ieq "python.exe" -and $cmd -like "*$resolvedRoot*" -and $cmd -match "backend\.main:app") -or
        ($name -ieq "node.exe" -and $cmd -match "\bvite\b" -and $cmd -match "--port\s+($portPattern)\b") -or
        ($name -ieq "node.exe" -and $cmd -like "*$resolvedRoot*" -and $cmd -match "\bvite\b") -or
        ($name -ieq "cmd.exe" -and $cmd -match "npm\s+run\s+dev" -and $cmd -match "--port\s+($portPattern)\b") -or
        ($name -ieq "cmd.exe" -and $cmd -match "\bvite\b" -and $cmd -match "--port\s+($portPattern)\b")

    if ($matchesProjectServer) {
        Add-Target -Set $targetIds -ProcessId ([int]$process.ProcessId)
        Add-Descendants -Set $targetIds -Processes $processes -ProcessId ([int]$process.ProcessId)
    }
}

if ($targetIds.Count -eq 0) {
    Write-Host "No existing backend/frontend processes were found."
} else {
    Write-Host "Stopping process trees: $($targetIds -join ', ')"
    foreach ($id in ($targetIds | Sort-Object -Descending)) {
        & taskkill.exe /F /T /PID $id >$null 2>$null
    }
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
do {
    Start-Sleep -Milliseconds 500
    $remaining = @(Get-ListeningProcessIds -TargetPorts $portList)
    if ($remaining.Count -eq 0) {
        Write-Host "Ports are free: $($portList -join ', ')"
        exit 0
    }
} while ((Get-Date) -lt $deadline)

Write-Host "ERROR: Ports are still in use after stopping: $($portList -join ', ')"
foreach ($id in $remaining) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$id"
    if ($process) {
        Write-Host "  PID $id $($process.Name): $($process.CommandLine)"
    } else {
        Write-Host "  PID ${id}: process information is unavailable"
    }
}
exit 1
