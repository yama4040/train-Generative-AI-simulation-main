[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,

    [string]$MajorVersion = "22"
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$resolvedProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$toolsDir = Join-Path $resolvedProjectRoot ".tools"
$nodeDir = Join-Path $toolsDir "node"
$nodeExe = Join-Path $nodeDir "node.exe"
$npmCmd = Join-Path $nodeDir "npm.cmd"

if ((Test-Path -LiteralPath $nodeExe) -and (Test-Path -LiteralPath $npmCmd)) {
    Write-Host "Using existing local Node.js: $nodeDir"
    exit 0
}

if (Test-Path -LiteralPath $nodeDir) {
    throw "Local Node.js directory exists but is incomplete: $nodeDir. Delete this directory and rerun setup.bat, or install Node.js 18+ manually."
}

New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null
$downloadDir = Join-Path $toolsDir "downloads"
New-Item -ItemType Directory -Force -Path $downloadDir | Out-Null

$distUrl = "https://nodejs.org/dist/latest-v$MajorVersion.x"
Write-Host "Fetching Node.js release metadata from $distUrl ..."
$shasums = Invoke-WebRequest -Uri "$distUrl/SHASUMS256.txt" -UseBasicParsing
$zipLine = ($shasums.Content -split "`n" | Where-Object { $_ -match "node-v[\d.]+-win-x64\.zip" } | Select-Object -First 1)

if (-not $zipLine) {
    throw "Could not find a Windows x64 Node.js zip in $distUrl."
}

$zipName = ($zipLine.Trim() -split "\s+")[-1]
$zipPath = Join-Path $downloadDir $zipName

if (-not (Test-Path -LiteralPath $zipPath)) {
    Write-Host "Downloading $zipName ..."
    Invoke-WebRequest -Uri "$distUrl/$zipName" -OutFile $zipPath -UseBasicParsing
}

$extractRoot = Join-Path $toolsDir ("node-extract-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $extractRoot | Out-Null

Write-Host "Extracting $zipName ..."
Expand-Archive -LiteralPath $zipPath -DestinationPath $extractRoot -Force
$extractedDir = Get-ChildItem -LiteralPath $extractRoot -Directory | Select-Object -First 1
if (-not $extractedDir) {
    throw "Failed to extract Node.js archive."
}

Move-Item -LiteralPath $extractedDir.FullName -Destination $nodeDir

if (Test-Path -LiteralPath $extractRoot) {
    Remove-Item -LiteralPath $extractRoot -ErrorAction SilentlyContinue
}

if (-not ((Test-Path -LiteralPath $nodeExe) -and (Test-Path -LiteralPath $npmCmd))) {
    throw "Node.js was extracted, but node.exe or npm.cmd was not found."
}

Write-Host "Installed local Node.js: $nodeDir"
