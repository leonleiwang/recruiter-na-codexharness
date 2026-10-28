# Start the portable MySQL instance that backs the recruiter-na resume DB.
#
# Why portable: the DSH pwsh shell is never elevated, so MySQL cannot be
# registered as a Windows service. This ZIP install runs as a plain process.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start-mysql.ps1
#
# Idempotent: if port 3306 is already served by mysqld nothing is started.
#
# Exit codes:
#   0  mysqld is listening on 3306 and speaks the MySQL protocol
#   1  mysqld.exe or my.ini is missing
#   2  mysqld was started but did not become ready within 30s
#   3  port 3306 is held by some other process (not a false "ready")
#
# NOTE: keep this file pure ASCII. Windows PowerShell 5.1 reads .ps1 files as
# ANSI unless they carry a UTF-8 BOM, so non-ASCII strings corrupt the parse.

$ErrorActionPreference = "Stop"

# $ErrorActionPreference='Stop' turns Write-Error into a terminating error, so
# "Write-Error ...; exit N" never reaches the exit and the caller always sees 1.
function Write-Fatal {
    param([string]$Message, [int]$Code)
    [Console]::Error.WriteLine($Message)
    exit $Code
}

$MysqlHome = "D:\MySQL\mysql-8.0.28-winx64"
$Mysqld    = Join-Path $MysqlHome "bin\mysqld.exe"
$Defaults  = "D:\MySQL\my.ini"
$Port      = 3306

if (-not (Test-Path -LiteralPath $Mysqld)) {
    Write-Fatal "mysqld not found: $Mysqld" 1
}
if (-not (Test-Path -LiteralPath $Defaults)) {
    Write-Fatal "my.ini not found: $Defaults" 1
}

# PID of whatever is listening on the port, or $null when the port is free.
function Get-PortOwnerPid {
    param([int]$P)
    try {
        $conn = Get-NetTCPConnection -LocalPort $P -State Listen -ErrorAction Stop |
                Select-Object -First 1
        return $conn.OwningProcess
    } catch {
        # Get-NetTCPConnection is unavailable on some hosts; fall back to netstat.
        try {
            $line = & netstat -ano -p tcp |
                    Select-String -Pattern (":" + $P + "\s+.*LISTENING") |
                    Select-Object -First 1
            if ($null -eq $line) { return $null }
            $fields = ($line.ToString().Trim() -split '\s+')
            if ($fields.Length -lt 5) { return $null }
            return [int]$fields[-1]
        } catch {
            return $null
        }
    }
}

function Get-ProcessName {
    param([int]$ProcessId)
    $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $proc) { return $null }
    return $proc.ProcessName
}

# A listening socket is not proof of readiness: any process can bind 3306, and
# mysqld itself can still be initialising. Require a MySQL server handshake
# before reporting ready.
#
# Wire format of the initial handshake packet (MySQL 8.0.28, verified against the
# live server): 3-byte little-endian payload length, 1-byte sequence id, then the
# protocol version. A real capture starts 4A 00 00 00 0A 38 2E 30 2E 32 38
# ("J.....8.0.28"), i.e. byte 4 is 0x0A, NOT byte 0. Checking byte 0 rejected a
# perfectly healthy server and made this script start a second mysqld.
function Test-MySqlHandshake {
    param([int]$P)
    $client = $null
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $pending = $client.BeginConnect('127.0.0.1', $P, $null, $null)
        if (-not $pending.AsyncWaitHandle.WaitOne(3000)) { return $false }
        $client.EndConnect($pending)
        $stream = $client.GetStream()
        $stream.ReadTimeout = 3000
        $buf = New-Object byte[] 6
        $read = 0
        while ($read -lt 6) {
            $n = $stream.Read($buf, $read, 6 - $read)
            if ($n -le 0) { break }
            $read += $n
        }
        if ($read -lt 5) { return $false }
        # buf[4] is the protocol version; buf[0] is tolerated for proxies that
        # rewrite the packet framing.
        if ($buf[4] -eq 10) { return $true }
        return ($buf[0] -eq 10)
    } catch {
        return $false
    } finally {
        if ($null -ne $client) { $client.Close() }
    }
}

$ownerPid = Get-PortOwnerPid -P $Port
# The handshake is the only trustworthy readiness signal, so it comes first.
# Deciding by process name alone misfires: on a host where Get-Process cannot
# resolve the PID that netstat reports, the owner name comes back empty and the
# script used to claim "port 3306 is used by '' (pid 35084), not mysqld".
$ownerName = $null
if ($null -ne $ownerPid) {
    $ownerName = Get-ProcessName -ProcessId $ownerPid
    if (Test-MySqlHandshake -P $Port) {
        if ($null -eq $ownerName) { $ownerName = "<name unavailable>" }
        Write-Host "[mysql] port $Port already serves the MySQL protocol (owner pid $ownerPid, '$ownerName') - nothing to do" -ForegroundColor Green
        exit 0
    }
    if ($null -ne $ownerName -and $ownerName -ne 'mysqld') {
        Write-Fatal "[mysql] port $Port is already in use by '$ownerName' (pid $ownerPid) and it does not speak the MySQL protocol. Refusing to report ready." 3
    }
}

if ($null -ne $ownerPid -and $ownerName -eq 'mysqld') {
    # mysqld already owns the port but has not finished initialising. Starting a
    # second one can only fail to bind, so just wait for this one.
    Write-Host "[mysql] mysqld (pid $ownerPid) owns port $Port but is not answering yet - waiting ..." -ForegroundColor Yellow
} else {
    Write-Host "[mysql] starting $Mysqld ..." -ForegroundColor Cyan
    Start-Process -FilePath $Mysqld -ArgumentList "--defaults-file=$Defaults" -WindowStyle Hidden
}

for ($i = 1; $i -le 30; $i++) {
    Start-Sleep -Milliseconds 1000
    $readyPid = Get-PortOwnerPid -P $Port
    if ($null -ne $readyPid -and (Test-MySqlHandshake -P $Port)) {
        Write-Host "[mysql] ready on 127.0.0.1:$Port (database piclaw, pid $readyPid)" -ForegroundColor Green
        exit 0
    }
}

Write-Fatal "[mysql] port $Port did not become ready within 30s - check D:\MySQL\data\*.err" 2
