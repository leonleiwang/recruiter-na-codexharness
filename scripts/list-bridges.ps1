Get-CimInstance Win32_Process -Filter "Name='node.exe'" |
  Where-Object { $_.CommandLine -like '*recruiter-na.mjs*' } |
  ForEach-Object { "PID=$($_.ProcessId) START=$($_.CreationDate) CMD=$($_.CommandLine)" }
