# =============================================================================
# start-recruiter-bridge.ps1
# Recruiter-Na DingTalk bridge launcher (PowerShell-native, ASCII-only).
#
# Why a new script:
#   The legacy scripts/start-local-ps1.ps1 was last edited in an environment
#   whose smart quotes leaked into the parser, which makes PowerShell report
#   "missing string terminator". This rewrite uses ASCII only and explicit
#   paths so it runs cleanly on Windows PowerShell 5.1 and pwsh 7+.
#
#   Because this file is ASCII-only it needs no UTF-8 BOM, which is the only
#   thing that makes a BOM-less .ps1 readable by Windows PowerShell 5.1.
#   scripts/start-local-ps1.ps1 does contain Chinese and carries a BOM.
#
# Usage (from any PowerShell):
#   powershell -ExecutionPolicy Bypass -File scripts\start-recruiter-bridge.ps1
#
# Stops:
#   Press Ctrl+C in the same window, or close the window.
# =============================================================================

$ErrorActionPreference = 'Stop'

# $ErrorActionPreference='Stop' turns Write-Error into a terminating error, so
# "Write-Error ...; exit N" never reaches the exit and the caller always sees 1.
# Every fatal path goes through here instead.
function Write-Fatal {
    param([string]$Message, [int]$Code)
    [Console]::Error.WriteLine($Message)
    exit $Code
}

# ----- Resolve project root -----
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$root = (Resolve-Path (Join-Path $scriptDir '..')).Path
Set-Location $root

# ----- Pre-flight: env file must exist -----
# Same RECRUITER_ENV_FILE override as start-local.sh:6, read from the inherited
# environment (the env file itself is not loaded yet).
$envFile = if ($env:RECRUITER_ENV_FILE) { $env:RECRUITER_ENV_FILE } else { Join-Path $root '.env.local' }
if (-not (Test-Path $envFile)) {
    Write-Fatal "Missing $envFile" 2
}

# ----- Single-instance guard -----
# One DingTalk bot may only have one Stream connection owner: a second instance
# steals messages from the first and the loser replies INTERNAL_UNKNOWN.
# The guard is a PID lock file (scripts/bridge-lock.ps1), not a WMI process query:
# Get-CimInstance is denied for non-admin sessions and used to kill all starters.
. (Join-Path $scriptDir 'bridge-lock.ps1')
$lockFile = Join-Path $root 'workspace/bridge.lock'
$lockStatus = Enter-BridgeLock -LockFile $lockFile -OwnerPid $PID
if ($lockStatus -eq 'held') {
    [Console]::Error.WriteLine("Refusing to start: a bridge instance is already running.")
    [Console]::Error.WriteLine("Run scripts\stop-local.cmd first, or set RECRUITER_ALLOW_MULTI=1 to skip this check.")
    exit 4
}
if ($lockStatus -eq 'unknown') {
    Write-Fatal "Cannot parse lock file $lockFile (corrupt or unreadable). Refusing to start: delete it or set RECRUITER_ALLOW_MULTI=1." 5
}

# ----- Load the env file into the process environment -----
# The env file contains non-ASCII paths; use UTF-8 explicitly so
# [Environment]::SetEnvironmentVariable is not subject to OEM codepage
# transcoding that would corrupt wide characters.
Get-Content -LiteralPath $envFile -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if (-not $line) { return }
    if ($line.StartsWith('#')) { return }
    if ($line -match '^([A-Z0-9_]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
    }
}

# ----- Derived paths (mirror runtime/rn defaults) -----
if (-not $env:RECRUITER_REQUEST_DB)    { $env:RECRUITER_REQUEST_DB    = Join-Path $root 'workspace/hiring_manager_requests.sqlite3' }
if (-not $env:CODEX_HOME)              { $env:CODEX_HOME              = Join-Path $root 'workspace/codex-home' }
if (-not $env:CODEX_WORKDIR)           { $env:CODEX_WORKDIR           = $root }
if (-not $env:RECRUITER_RESUME_URL_DIR){ $env:RECRUITER_RESUME_URL_DIR= Join-Path $root 'workspace/resume-url' }
if (-not $env:RECRUITER_DEDUPE_DIR)    { $env:RECRUITER_DEDUPE_DIR     = Join-Path $root 'workspace/.im-dedupe' }
if (-not $env:RECRUITER_PROGRESS_DIR)  { $env:RECRUITER_PROGRESS_DIR   = Join-Path $root 'workspace/.progress' }

$env:PYTHONPATH       = Join-Path $root 'runtime'
$env:PYTHONUTF8       = '1'
$env:PYTHONIOENCODING = 'utf-8'

# ----- Required credentials guard -----
$fatal = @()
if (-not $env:DINGTALK_CLIENT_ID)     { $fatal += 'DINGTALK_CLIENT_ID' }
if (-not $env:DINGTALK_CLIENT_SECRET) { $fatal += 'DINGTALK_CLIENT_SECRET' }
if ($fatal.Count -gt 0) {
    Write-Fatal "Missing required env: $($fatal -join ', ')" 3
}

# ----- Ensure runtime directories exist -----
New-Item -ItemType Directory -Force -Path (Split-Path $env:RECRUITER_REQUEST_DB) | Out-Null
New-Item -ItemType Directory -Force -Path $env:CODEX_HOME                       | Out-Null
New-Item -ItemType Directory -Force -Path $env:RECRUITER_RESUME_URL_DIR         | Out-Null
New-Item -ItemType Directory -Force -Path $env:RECRUITER_DEDUPE_DIR             | Out-Null
New-Item -ItemType Directory -Force -Path $env:RECRUITER_PROGRESS_DIR           | Out-Null

# ----- Ensure the MySQL backing store is running (RN_BACKEND=mysql) -----
# MySQL here is a portable ZIP install (no Windows service), so it can stop with
# the shell that launched it. Bring it up before the bridge so the first DingTalk
# message does not fail with "Can't connect to MySQL server on 127.0.0.1".
if ($env:RN_BACKEND -eq 'mysql') {
    $mysqlScript = Join-Path $scriptDir 'start-mysql.ps1'
    if (Test-Path -LiteralPath $mysqlScript) {
        Write-Host '[bridge] ensuring MySQL is up on 127.0.0.1:3306 ...'
        & powershell -NoProfile -ExecutionPolicy Bypass -File $mysqlScript
        if ($LASTEXITCODE -ne 0) {
            Write-Warning '[bridge] MySQL did not come up. Resume screening will fail until D:\MySQL is running.'
        }
    } else {
        Write-Warning "[bridge] $mysqlScript not found; assuming MySQL is already running."
    }
}

# ----- Summary banner -----
Write-Host ''
Write-Host '=== Recruiter-Na DingTalk bridge ==='
Write-Host ("root              = {0}" -f $root)
Write-Host ("codex_home        = {0}" -f $env:CODEX_HOME)
Write-Host ("python            = {0}" -f ($(if ($env:RECRUITER_PYTHON_BIN) { $env:RECRUITER_PYTHON_BIN } else { 'python' })))
Write-Host ("request_db        = {0}" -f $env:RECRUITER_REQUEST_DB)
Write-Host ("dedupe            = {0}" -f $env:RECRUITER_DEDUPE_DIR)
Write-Host ("progress          = {0}" -f $env:RECRUITER_PROGRESS_DIR)
Write-Host ("resume_url_dir    = {0}" -f $env:RECRUITER_RESUME_URL_DIR)
Write-Host ''
Write-Host 'Expect:'
Write-Host '  Recruiter-Na v5.0.2 DingTalk bridge started; pid=...'
Write-Host '  [time] connect success'
Write-Host ''
Write-Host 'Press Ctrl+C to stop.'
Write-Host ''

# ----- Foreground run -----
$code = 0
try {
    & node "$root/im/recruiter-na.mjs"
    $code = $LASTEXITCODE
} finally {
    Exit-BridgeLock -LockFile $lockFile
}
exit $code
