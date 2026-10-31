# Helpers shared by the ci/jenkins/*.ps1 scripts (dot-source this file).

# Run a native command (git, pip, python, plink) and fail on a non-zero exit
# code. Windows PowerShell 5.1 turns anything a native command writes to
# stderr -- git's and pip's progress and warnings -- into an error record
# when output is redirected, as it is under Jenkins, which would stop a
# script running with $ErrorActionPreference = 'Stop'. So stderr is merged
# into the output as plain text and only the exit code decides.
function Invoke-Native {
  param([Parameter(Mandatory)] [string] $Exe, [Parameter(ValueFromRemainingArguments)] [string[]] $Arguments)
  $saved = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    & $Exe @Arguments 2>&1 | ForEach-Object { "$_" }
    $code = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $saved
  }
  if ($code) { throw "$Exe $($Arguments -join ' ') failed with exit code $code" }
}

# KEY=value pairs of an .env file, as a hashtable (no variable expansion).
function Read-DotEnv([string] $Path) {
  $values = @{}
  foreach ($line in Get-Content $Path) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
      $values[$matches[1]] = $matches[2].Trim('"').Trim("'")
    }
  }
  $values
}

# HTTP status of a URL without following redirects (0 when unreachable).
function Get-HttpStatus([string] $Url) {
  try {
    $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -MaximumRedirection 0 -TimeoutSec 5 -ErrorAction Stop
    return [int] $r.StatusCode
  } catch {
    if ($_.Exception.Response) { return [int] $_.Exception.Response.StatusCode }
    return 0
  }
}
