<#
  Deploy one main commit to production (the EC2 host), after approval in
  Jenkins:

    1. The commit's files must already have run in the development copy
       (its tree is in <DevHome>\verified-trees.txt) -- unless -Hotfix.
    2. Every GitHub Actions push run for the commit must have passed
       (waits while they're still running).
    3. Over SSH, the server's own deploy script deploys exactly that commit:
       database backup, checkout, restart, /healthz check, automatic
       rollback (scripts/ec2_autodeploy.sh with HRZEST_DEPLOY_SHA).
    4. Confirms the server now runs the commit.

  -CheckOnly just answers step 1 (exit 0 = ran in development).

  Settings (Jenkins passes them in): HRZEST_PROD_HOST (user@host),
  HRZEST_PROD_HOSTKEY (the server's SSH host-key fingerprint),
  HRZEST_PROD_SSH_KEY (PuTTY .ppk), HRZEST_PLINK (plink.exe).
#>
param(
  [Parameter(Mandatory)] [string] $Commit,
  [switch] $Hotfix,
  [switch] $CheckOnly,
  [string] $DevHome = $env:HRZEST_DEV_HOME,
  [string] $GitHubRepo = 'Nithin-5310v/HRZEST-2.0',
  [string] $AppDir = '/home/ubuntu/apps/HRZEST',
  [int] $CiWaitMinutes = 45
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\common.ps1"

$short = $Commit.Substring(0, 7)

# ── 1. Ran in development? ──
$tree = (Invoke-Native git rev-parse "$Commit^{tree}" | Select-Object -Last 1).Trim()
$verifiedFile = Join-Path $DevHome 'verified-trees.txt'
$verified = (Test-Path $verifiedFile) -and [bool](Select-String -Path $verifiedFile -Pattern "^$tree " -Quiet)
if ($CheckOnly) {
  if ($verified) { Write-Host "$short ran in the development copy." ; exit 0 }
  Write-Host "$short (tree $($tree.Substring(0, 7))) has NOT run in the development copy."
  exit 1
}
if (-not $verified) {
  if (-not $Hotfix) {
    throw "Refusing to deploy ${short}: this code never ran in the development copy. Merge it through develop first, or approve it as a hotfix."
  }
  Write-Warning "Deploying $short as a HOTFIX: it never ran in the development copy."
}

# ── 2. GitHub Actions ──
function Get-CiState([string] $sha) {
  $url = "https://api.github.com/repos/$GitHubRepo/actions/runs?head_sha=$sha&event=push&per_page=100"
  $runs = (Invoke-RestMethod -Uri $url -Headers @{ Accept = 'application/vnd.github+json' } -TimeoutSec 30).workflow_runs
  # Newest run per workflow: a re-run replaces a failed attempt.
  $latest = $runs | Group-Object workflow_id | ForEach-Object { $_.Group | Sort-Object id -Descending | Select-Object -First 1 }
  if (-not $latest -or ($latest | Where-Object { $_.status -ne 'completed' })) { return 'pending' }
  $bad = $latest | Where-Object { $_.conclusion -notin 'success', 'skipped', 'neutral' }
  if ($bad) { return 'failed: ' + (($bad | ForEach-Object { $_.name }) -join ', ') }
  return 'success'
}

$deadline = (Get-Date).AddMinutes($CiWaitMinutes)
while ($true) {
  try { $state = Get-CiState $Commit } catch { $state = "pending (GitHub API: $($_.Exception.Message))" }
  if ($state -eq 'success') { Write-Host "GitHub Actions passed for $short."; break }
  if ($state -like 'failed*') { throw "Not deploying ${short}: GitHub Actions $state" }
  if ((Get-Date) -gt $deadline) { throw "Not deploying ${short}: GitHub Actions still not finished after $CiWaitMinutes minutes." }
  Write-Host "Waiting for GitHub Actions on $short ($state)..."
  Start-Sleep -Seconds 60
}

# ── 3 + 4. Deploy on the server ──
foreach ($name in 'HRZEST_PROD_HOST', 'HRZEST_PROD_HOSTKEY', 'HRZEST_PROD_SSH_KEY', 'HRZEST_PLINK') {
  if (-not [Environment]::GetEnvironmentVariable($name)) { throw "$name is not set." }
}
$remote = "HRZEST_DEPLOY_SHA=$Commit /usr/local/bin/hrzest-autodeploy; rc=`$?; " +
          "echo `"server is running: `$(git -C $AppDir rev-parse HEAD)`"; exit `$rc"
Write-Host "Deploying $short to production ($env:HRZEST_PROD_HOST)"
# Not Invoke-Native: the server's output must be shown even when the deploy fails.
$ErrorActionPreference = 'Continue'
$output = & $env:HRZEST_PLINK -batch -ssh -hostkey $env:HRZEST_PROD_HOSTKEY -i $env:HRZEST_PROD_SSH_KEY $env:HRZEST_PROD_HOST $remote 2>&1 |
  ForEach-Object { "$_" }
$code = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
$output | ForEach-Object { Write-Host $_ }
if ($code) { throw "The server's deploy failed (exit code $code) -- see its output above. It rolls back by itself when the new commit isn't healthy." }

$running = ($output | Select-String 'server is running: ([0-9a-f]{40})' | Select-Object -Last 1).Matches.Groups[1].Value
if ($running -ne $Commit) {
  throw "Production is running $running, not $Commit -- the deploy did not go through (see the server's output above)."
}
Write-Host "Production is running $short."
