<#
  Release one main commit to production (the EC2 host), after approval in
  Jenkins:

    1. The commit's files must already have run in the development copy
       (its tree is in <DevHome>\verified-trees.txt) -- unless -Hotfix.
    2. The commit must be the result of a merged GitHub PR into main (not a
       direct push), and every GitHub Actions push run for it must have
       passed (waits while they're still running). -Hotfix doesn't skip this.
    3. Moves the `production` branch on GitHub to the commit. The server
       checks that branch every 2 minutes and deploys it itself -- database
       backup, checkout, restart, /healthz check, automatic rollback
       (scripts/ec2_autodeploy.sh in production-branch mode) -- so nothing
       here needs SSH access to it.
    4. Waits until the live site confirms it runs the commit
       (/healthz?commit=<sha> answers commit_match: true).

  -CheckOnly just answers step 1 (exit 0 = ran in development). With
  -WaitForDevelop it first waits for a develop build of the same code that
  is still deploying (a merge into develop followed right away by develop
  into main builds both side by side).

  -CheckMerged just answers step 2 (exit 0 = merged PR, Actions passed), so
  Jenkins can stop before asking for approval.

  Moving the branch uses this Windows user's GitHub login (Git Credential
  Manager), the same one `git push` uses here.
#>
param(
  [Parameter(Mandatory)] [string] $Commit,
  [switch] $Hotfix,
  [switch] $CheckOnly,
  [switch] $CheckMerged,
  [switch] $WaitForDevelop,
  [string] $DevHome = $env:HRZEST_DEV_HOME,
  [string] $GitHubRepo = 'Nithin-5310v/HRZEST-2.0',
  [string] $ProdUrl = $(if ($env:HRZEST_PROD_URL) { $env:HRZEST_PROD_URL } else { 'https://hrzest.com' }),
  [int] $CiWaitMinutes = 45,
  [int] $DevWaitMinutes = 40,
  [int] $LiveWaitMinutes = 25
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\common.ps1"
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$short = $Commit.Substring(0, 7)

# ── Step 2's checks (defined first: -CheckMerged runs them alone) ──
function Get-MergedPr([string] $sha) {
  # The PR whose merge into main produced this exact commit. A direct push to
  # main has no such PR, so it's never released.
  $url = "https://api.github.com/repos/$GitHubRepo/commits/$sha/pulls"
  $prs = Invoke-RestMethod -Uri $url -Headers @{ Accept = 'application/vnd.github+json' } -TimeoutSec 30
  $prs | Where-Object { $_.merged_at -and $_.base.ref -eq 'main' -and $_.merge_commit_sha -eq $sha } | Select-Object -First 1
}

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

function Assert-MergedAndGreen {
  $pr = Get-MergedPr $Commit
  if (-not $pr) {
    throw "Not deploying ${short}: it isn't the merge commit of a completed GitHub PR into main (a direct push, or the PR isn't merged). Merge a PR into main first."
  }
  Write-Host "$short is the merge of PR #$($pr.number) ($($pr.title))."

  $deadline = (Get-Date).AddMinutes($CiWaitMinutes)
  while ($true) {
    try { $state = Get-CiState $Commit } catch { $state = "pending (GitHub API: $($_.Exception.Message))" }
    if ($state -eq 'success') { Write-Host "GitHub Actions passed for $short."; return }
    if ($state -like 'failed*') { throw "Not deploying ${short}: GitHub Actions $state" }
    if ((Get-Date) -gt $deadline) { throw "Not deploying ${short}: GitHub Actions still not finished after $CiWaitMinutes minutes." }
    Write-Host "Waiting for GitHub Actions on $short ($state)..."
    Start-Sleep -Seconds 60
  }
}

if ($CheckMerged) {
  try { Assert-MergedAndGreen } catch { Write-Host $_.Exception.Message; exit 1 }
  exit 0
}

# ── 1. Ran in development? ──
$tree = (Invoke-Native git rev-parse "$Commit^{tree}" | Select-Object -Last 1).Trim()
$verifiedFile = Join-Path $DevHome 'verified-trees.txt'
function Test-RanInDevelopment {
  (Test-Path $verifiedFile) -and [bool](Select-String -Path $verifiedFile -Pattern "^$tree " -Quiet)
}
$verified = Test-RanInDevelopment
if ($CheckOnly) {
  if (-not $verified -and $WaitForDevelop) {
    Invoke-Native git fetch --quiet "https://github.com/$GitHubRepo.git" develop
    $developTree = (Invoke-Native git rev-parse "FETCH_HEAD^{tree}" | Select-Object -Last 1).Trim()
    if ($developTree -eq $tree) {
      Write-Host "develop has this same code; waiting for its build to finish deploying it to the development copy..."
      $deadline = (Get-Date).AddMinutes($DevWaitMinutes)
      while (-not $verified -and (Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 30
        $verified = Test-RanInDevelopment
      }
    }
  }
  if ($verified) { Write-Host "$short ran in the development copy."; exit 0 }
  Write-Host "$short (tree $($tree.Substring(0, 7))) has NOT run in the development copy."
  exit 1
}
if (-not $verified) {
  if (-not $Hotfix) {
    throw "Refusing to deploy ${short}: this code never ran in the development copy. Merge it through develop first, or approve it as a hotfix."
  }
  Write-Warning "Deploying $short as a HOTFIX: it never ran in the development copy."
}

# ── 2. Merged PR + GitHub Actions (checked again: the build may have waited days for approval) ──
Assert-MergedAndGreen

# ── 3. Approve: move the production branch ──
Write-Host "Moving the production branch on GitHub to $short"
$env:GIT_TERMINAL_PROMPT = '0'   # fail, never wait for a password prompt
$env:GCM_INTERACTIVE = 'never'
Invoke-Native git push --quiet "https://github.com/$GitHubRepo.git" "+${Commit}:refs/heads/production"

# ── 4. Wait until it's live ──
Write-Host "Waiting for $ProdUrl to run $short (the server checks every 2 minutes, backs up the database, deploys and checks itself)..."
$deadline = (Get-Date).AddMinutes($LiveWaitMinutes)
while ($true) {
  try {
    $live = (Invoke-RestMethod -Uri "$ProdUrl/healthz?commit=$Commit" -TimeoutSec 20).commit_match -eq $true
  } catch {
    $live = $false   # restarting, or briefly unreachable
  }
  if ($live) { break }
  if ((Get-Date) -gt $deadline) {
    throw "Production did not switch to $short within $LiveWaitMinutes minutes. The server rolls a failed deploy back by itself; its log: 'journalctl -u hrzest-autodeploy' on the server."
  }
  Start-Sleep -Seconds 20
}
Write-Host "Production is running $short."
