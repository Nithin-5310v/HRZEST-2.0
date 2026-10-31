<#
  Deploy one develop commit to the development copy on this PC:
  <DevHome>\app (a clone of the repo, checked out at that commit), its own
  virtualenv and its own PostgreSQL database (DB_NAME in <DevHome>\.env,
  e.g. employee_attendance_dev). If the new commit doesn't come up healthy,
  the previous one is put back.

  Each healthy deploy records the commit's file tree in
  <DevHome>\verified-trees.txt -- deploy_prod.ps1 only lets code onto
  production once that exact tree has run here.

    .\ci\jenkins\deploy_dev.ps1 -Commit <sha>
#>
param(
  [Parameter(Mandatory)] [string] $Commit,
  [string] $DevHome = $env:HRZEST_DEV_HOME,
  [string] $Repo = 'https://github.com/Nithin-5310v/HRZEST-2.0.git',
  [int] $HealthWaitSeconds = 240
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\common.ps1"

if (-not $DevHome) { throw 'HRZEST_DEV_HOME is not set.' }
$app = Join-Path $DevHome 'app'
$envFile = Join-Path $DevHome '.env'
$deployedFile = Join-Path $DevHome 'deployed.txt'
if (-not (Test-Path $envFile)) { throw "$envFile is missing -- run ci\jenkins\install-jenkins.ps1 first." }
$port = (Read-DotEnv $envFile)['PORT']
if (-not $port) { throw "PORT is not set in $envFile." }
# The app redirects 127.0.0.1 to localhost (app.py), so ask localhost directly.
$healthUrl = "http://localhost:$port/healthz"

if (-not (Test-Path "$app\.git")) {
  Write-Host "Cloning $Repo into $app"
  Invoke-Native git clone --quiet $Repo $app
}
Invoke-Native git -C $app fetch --quiet --prune origin
$previous = if (Test-Path $deployedFile) { (Get-Content $deployedFile -Raw).Trim() } else { $null }

function Deploy([string] $sha) {
  Invoke-Native git -C $app -c advice.detachedHead=false checkout --quiet --force --detach $sha
  # .env is git-ignored, so checkouts never touch it; refresh it from the master copy.
  Copy-Item $envFile "$app\.env" -Force
  & "$PSScriptRoot\setup_venv.ps1" -Venv (Join-Path $DevHome 'venv') -Root $app
  Invoke-Native (Join-Path $DevHome 'venv\Scripts\python.exe') "$PSScriptRoot\ensure_database.py" "$app\.env"
  & "$PSScriptRoot\start_dev_server.ps1" -DevHome $DevHome
}

function Wait-Healthy {
  $deadline = (Get-Date).AddSeconds($HealthWaitSeconds)
  while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 3
    if ((Get-HttpStatus $healthUrl) -eq 200) { return $true }
  }
  return $false
}

$short = $Commit.Substring(0, 7)
Write-Host "Deploying $short to the development copy ($healthUrl)"
# The logon starter runs the installed copy, so keep it current.
Copy-Item "$PSScriptRoot\start_dev_server.ps1" $DevHome -Force

$ok = $false
try { Deploy $Commit; $ok = Wait-Healthy } catch { Write-Host "Deploy step failed: $_" }

if ($ok) {
  Set-Content $deployedFile $Commit -NoNewline
  $tree = (Invoke-Native git -C $app rev-parse "$Commit^{tree}" | Select-Object -Last 1).Trim()
  Add-Content (Join-Path $DevHome 'verified-trees.txt') "$tree $Commit $((Get-Date).ToUniversalTime().ToString('s'))Z"
  Write-Host "Development copy is running $short -- open http://localhost:$port/login"
  exit 0
}

Write-Host "---- last lines of the development copy's error log ----"
Get-Content (Join-Path $DevHome 'logs\server.err.log') -Tail 40 -ErrorAction SilentlyContinue
if ($previous -and $previous -ne $Commit) {
  Write-Host "Putting the previous commit $($previous.Substring(0, 7)) back"
  try { Deploy $previous; if (Wait-Healthy) { Write-Host 'Previous commit is running again.' } } catch { Write-Host "Rollback failed: $_" }
}
throw "Deploy of $short to the development copy failed: $healthUrl did not answer 200 within $HealthWaitSeconds s."
