# Recruiter-Na local bridge launcher (PowerShell-native).
# Equivalent to scripts/start-local.sh but skips Git Bash to avoid the
# "couldn't create signal pipe" fatal when launched as a background job.
#
# NOTE: this file contains non-ASCII text, so it MUST keep a UTF-8 BOM.
# Windows PowerShell 5.1 reads a BOM-less .ps1 as ANSI (GBK on zh-CN),
# which mangles both these messages and any Chinese path read from .env.local.

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot\..").Path

# $ErrorActionPreference='Stop' turns Write-Error into a terminating error, so
# "Write-Error ...; exit N" never reaches the exit and the caller always sees 1.
# Every fatal path goes through here instead.
function Write-Fatal {
    param([string]$Message, [int]$Code)
    [Console]::Error.WriteLine($Message)
    exit $Code
}

# Same env-file override as start-local.sh:6, read from the inherited
# environment (the .env.local file itself is not loaded yet).
$envFile = if ($env:RECRUITER_ENV_FILE) { $env:RECRUITER_ENV_FILE } else { Join-Path $root '.env.local' }

if (-not (Test-Path $envFile)) {
    Write-Fatal "缺少 $envFile" 2
}

# One DingTalk bot may only have one Stream connection owner: a second instance
# steals messages from the first and the loser replies INTERNAL_UNKNOWN.
# The guard is a PID lock file (scripts/bridge-lock.ps1), not a WMI process query:
# Get-CimInstance is denied for non-admin sessions and used to kill all starters.
. (Join-Path $root 'scripts\bridge-lock.ps1')
$lockFile = Join-Path $root 'workspace\bridge.lock'
$lockStatus = Enter-BridgeLock -LockFile $lockFile -OwnerPid $PID
if ($lockStatus -eq 'held') {
    [Console]::Error.WriteLine("已有桥接实例在运行，拒绝重复启动。")
    [Console]::Error.WriteLine("先执行：scripts\stop-local.cmd （或设 RECRUITER_ALLOW_MULTI=1 跳过检查）")
    exit 4
}
if ($lockStatus -eq 'unknown') {
    Write-Fatal "锁文件 $lockFile 无法解析（内容损坏或无权限）。为避免重复启动，拒绝继续：先删除该文件或设 RECRUITER_ALLOW_MULTI=1。" 5
}

# Load .env.local into the current process (overwrite unset with empty).
# -Encoding UTF8 is required: RESUME_JSONL_PATH is a Chinese path, and 5.1
# would otherwise decode it as GBK and pass a mangled path to the backend.
Get-Content -LiteralPath $envFile -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith('#')) { return }
    if ($line -match '^([A-Z0-9_]+)=(.*)$') {
        $name = $Matches[1]
        $val = $Matches[2]
        [Environment]::SetEnvironmentVariable($name, $val, 'Process')
    }
}

# Derived paths (mirror start-local.sh).
if (-not $env:RECRUITER_REQUEST_DB)   { $env:RECRUITER_REQUEST_DB = Join-Path $root 'workspace/hiring_manager_requests.sqlite3' }
if (-not $env:CODEX_HOME)             { $env:CODEX_HOME           = Join-Path $root 'workspace/codex-home' }
if (-not $env:CODEX_WORKDIR)          { $env:CODEX_WORKDIR        = $root }
if (-not $env:RECRUITER_RESUME_URL_DIR){ $env:RECRUITER_RESUME_URL_DIR = Join-Path $root 'workspace/resume-url' }
if (-not $env:RECRUITER_DEDUPE_DIR)   { $env:RECRUITER_DEDUPE_DIR  = Join-Path $root 'workspace/.im-dedupe' }
if (-not $env:RECRUITER_PROGRESS_DIR) { $env:RECRUITER_PROGRESS_DIR= Join-Path $root 'workspace/.progress' }

$env:PYTHONPATH      = Join-Path $root 'runtime'
$env:PYTHONUTF8      = '1'
$env:PYTHONIOENCODING= 'utf-8'

# Pre-flight checks.
$fatal = @()
if (-not $env:DINGTALK_CLIENT_ID)     { $fatal += 'DINGTALK_CLIENT_ID' }
if (-not $env:DINGTALK_CLIENT_SECRET) { $fatal += 'DINGTALK_CLIENT_SECRET' }
if ($fatal.Count -gt 0) {
    Write-Fatal "缺少必需变量: $($fatal -join ', ')" 3
}

New-Item -ItemType Directory -Force -Path (Split-Path $env:RECRUITER_REQUEST_DB) | Out-Null
New-Item -ItemType Directory -Force -Path $env:CODEX_HOME | Out-Null
New-Item -ItemType Directory -Force -Path $env:RECRUITER_RESUME_URL_DIR | Out-Null
New-Item -ItemType Directory -Force -Path $env:RECRUITER_DEDUPE_DIR | Out-Null
New-Item -ItemType Directory -Force -Path $env:RECRUITER_PROGRESS_DIR | Out-Null

Write-Host "启动 Recruiter-Na 桥接（PowerShell-native）"
Write-Host "  root=$root"
Write-Host "  codex_home=$($env:CODEX_HOME)"
Write-Host "  python=$($env:RECRUITER_PYTHON_BIN)"
Write-Host "  request_db=$($env:RECRUITER_REQUEST_DB)"

Push-Location $root
try {
    & node "$root/im/recruiter-na.mjs"
    $code = $LASTEXITCODE
} finally {
    Pop-Location
    Exit-BridgeLock -LockFile $lockFile
}

exit $code
