# bridge-lock.ps1 - single-instance guard shared by the recruiter bridge starters.
#
# WHY A LOCK FILE INSTEAD OF A PROCESS QUERY:
#   The DingTalk Stream client is an *outbound* connection, so there is no
#   listening port to probe. The only WMI way to find it is
#   Get-CimInstance Win32_Process, which is denied for a non-admin session
#   (access denied). When that probe was the start gate, all three start
#   scripts died with exit 5 on a normal desktop. A lock file is checked with
#   Get-Process -Id, which needs no privileges, and a lock whose PID is dead is
#   reclaimed instead of blocking forever.
#
# Keep this file pure ASCII (no BOM needed): Windows PowerShell 5.1 reads .ps1
# as ANSI unless the file carries a UTF-8 BOM.
#
# Returns a status string:
#   skipped  - RECRUITER_ALLOW_MULTI=1, no lock taken
#   acquired - this process now owns the lock (caller MUST call Exit-BridgeLock)
#   held     - a live instance owns it; caller exits 4
#   unknown  - the lock exists but is unreadable/garbage; caller exits 5 (fail closed)

function Enter-BridgeLock {
    param(
        [Parameter(Mandatory = $true)][string]$LockFile,
        [Parameter(Mandatory = $true)][int]$OwnerPid
    )

    if ($env:RECRUITER_ALLOW_MULTI -eq '1') { return 'skipped' }

    $lockPid = 0
    if (Test-Path -LiteralPath $LockFile) {
        $raw = $null
        try { $raw = Get-Content -LiteralPath $LockFile -Raw -ErrorAction Stop } catch { $raw = $null }
        if ($null -ne $raw -and $raw -match 'PID=(\d+)') {
            $lockPid = [int]$Matches[1]
        } elseif ($null -ne $raw -and $raw.Trim()) {
            # Non-empty but unreadable content: unknown state, never guess.
            return 'unknown'
        }
        if ($lockPid -gt 0) {
            # Get-Process needs no WMI rights, unlike Get-CimInstance.
            $live = Get-Process -Id $lockPid -ErrorAction SilentlyContinue
            if ($live) { return 'held' }
        }
        # Owner is dead, or the file is empty/leftover: reclaim it.
        try { Remove-Item -LiteralPath $LockFile -Force -ErrorAction Stop } catch { return 'unknown' }
    }

    $dir = Split-Path -Parent $LockFile
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    try {
        # CreateNew is the atomic "claim": it throws if the file appeared in between.
        $stream = [System.IO.File]::Open($LockFile, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    } catch {
        return 'held'
    }
    try {
        $bytes = [System.Text.Encoding]::ASCII.GetBytes("PID=$OwnerPid`n")
        $stream.Write($bytes, 0, $bytes.Length)
    } finally {
        $stream.Close()
    }
    return 'acquired'
}

function Exit-BridgeLock {
    param([string]$LockFile)
    if (-not $LockFile) { return }
    try { Remove-Item -LiteralPath $LockFile -Force -ErrorAction Stop } catch { }
}
