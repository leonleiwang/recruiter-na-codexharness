# List running Recruiter-Na bridge processes, one "PID=... START=... CMD=..." line
# per instance. Used by the start-*/stop-* scripts as the single-instance probe.
#
# Exit codes:
#   0  the probe worked; the output lists zero or more instances
#   4  the probe itself failed, so the caller cannot tell whether a bridge runs
#
# Callers must treat exit 4 as "unknown" and NOT as "nothing is running":
# a silent Win32_Process failure would otherwise let a second DingTalk Stream
# consumer start and steal messages from the live one.

$ErrorActionPreference = 'Stop'

try {
    $rows = @(Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction Stop |
        Where-Object { $_.CommandLine -like '*recruiter-na.mjs*' } |
        ForEach-Object { "$($_.ProcessId) START=$($_.CreationDate) CMD=$($_.CommandLine)" })
} catch {
    [Console]::Error.WriteLine("list-bridges: Win32_Process query failed: $($_.Exception.Message)")
    [Console]::Error.WriteLine("list-bridges: cannot determine whether a bridge is already running.")
    exit 4
}

$rows | ForEach-Object { "PID=$_" }
exit 0
