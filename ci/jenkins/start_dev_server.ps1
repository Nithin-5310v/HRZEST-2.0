<#
  (Re)start the development copy: `python run_dev.py` from <DevHome>\app
  with <DevHome>\venv, in the background, logging to <DevHome>\logs. The
  port comes from app\.env (PORT).

  deploy_dev.ps1 runs this after each deploy; Jenkins' start script runs it
  at logon (-OnlyIfStopped) so the copy comes back after a reboot.
#>
param(
  [string] $DevHome = $env:HRZEST_DEV_HOME,
  [switch] $OnlyIfStopped
)
$ErrorActionPreference = 'Stop'

$app = Join-Path $DevHome 'app'
$python = Join-Path $DevHome 'venv\Scripts\python.exe'
$pidFile = Join-Path $DevHome 'server.pid'
$logs = Join-Path $DevHome 'logs'

if (-not (Test-Path "$app\run_dev.py") -or -not (Test-Path $python) -or -not (Test-Path "$app\.env")) {
  Write-Host "Nothing deployed to $DevHome yet."
  exit 0
}
$port = (Select-String -Path "$app\.env" -Pattern '^\s*PORT\s*=\s*(\d+)' | Select-Object -Last 1).Matches.Groups[1].Value

# What listens on the port, if it is a run_dev.py (ours, not something else).
function Get-PortServer {
  $owner = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $owner) { return $null }
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($owner.OwningProcess)"
  if ($proc -and $proc.CommandLine -like '*run_dev.py*') { return $proc }
  throw "Port $port is taken by another program ($($proc.Name), PID $($owner.OwningProcess))."
}

$listening = Get-PortServer
if ($listening -and $OnlyIfStopped) { Write-Host "Development copy already running on port $port."; exit 0 }

# The server runs as cmd.exe -> venv python.exe (a launcher) -> the real
# python.exe, so stop the whole tree from the cmd.exe we started.
if (Test-Path $pidFile) {
  $wrapper = Get-CimInstance Win32_Process -Filter "ProcessId=$([int](Get-Content $pidFile -Raw))"
  if ($wrapper -and $wrapper.CommandLine -like '*run_dev.py*') {
    Write-Host "Stopping the development copy (PID $($wrapper.ProcessId))"
    & taskkill.exe /T /F /PID $wrapper.ProcessId | Out-Null
  }
}
# Anything still holding the port (e.g. started by hand) goes too.
for ($i = 0; $i -lt 30; $i++) {
  $listening = Get-PortServer
  if (-not $listening) { break }
  & taskkill.exe /T /F /PID $listening.ProcessId | Out-Null
  Start-Sleep -Milliseconds 500
}
if (Get-PortServer) { throw "Could not stop the development copy on port $port." }

New-Item -ItemType Directory -Force $logs | Out-Null
foreach ($log in 'server.out.log', 'server.err.log') {
  if (Test-Path "$logs\$log") { Move-Item "$logs\$log" "$logs\$log.previous" -Force }
}

# Started through WMI, not Start-Process: the server must outlive the
# Jenkins build that deploys it (Jenkins kills what a build leaves behind)
# and must not inherit the build's output handles, which would keep the
# build's log stream open. A WMI-created process is fully independent.
$command = "cmd.exe /d /c `"set PYTHONUTF8=1&& `"$python`" run_dev.py 1>`"$logs\server.out.log`" 2>`"$logs\server.err.log`"`""
$result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $command; CurrentDirectory = $app }
if ($result.ReturnValue -ne 0) { throw "Could not start the development copy (Win32_Process.Create returned $($result.ReturnValue))." }
Set-Content $pidFile $result.ProcessId -NoNewline
Write-Host "Started the development copy on port $port (PID $($result.ProcessId)); logs in $logs"
