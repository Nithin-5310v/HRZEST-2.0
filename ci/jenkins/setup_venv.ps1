<#
  Create or update a Python virtualenv for HRZEST on Windows (Jenkins'
  test environment, and the development copy's app environment).

  requirements.txt names face-recognition, which needs dlib -- and dlib has
  no Windows wheels, so pip would try to compile it. dlib-bin is the same
  library prebuilt; face-recognition goes in without its dlib dependency.

  Skips pip entirely when the requirements files haven't changed since the
  last run (hash kept in the venv).

    .\ci\jenkins\setup_venv.ps1 -Venv C:\Jenkins\venvs\ci -Dev
#>
param(
  [Parameter(Mandatory)] [string] $Venv,
  [string] $Root = (Resolve-Path "$PSScriptRoot\..\.."),
  [switch] $Dev   # also requirements-dev.txt (pytest, flake8, ...)
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\common.ps1"

$python = $env:HRZEST_PYTHON
if (-not $python) { throw 'HRZEST_PYTHON is not set (the Python 3.12 python.exe to build virtualenvs from).' }

$files = @("$Root\requirements.txt")
if ($Dev) { $files += "$Root\requirements-dev.txt" }
$hash = ($files | ForEach-Object { (Get-FileHash $_ -Algorithm SHA256).Hash }) -join '+'
$stamp = Join-Path $Venv '.requirements-hash'
$venvPython = Join-Path $Venv 'Scripts\python.exe'

if ((Test-Path $venvPython) -and (Test-Path $stamp) -and ((Get-Content $stamp -Raw).Trim() -eq $hash)) {
  Write-Host "Python environment $Venv is up to date."
  exit 0
}

if (-not (Test-Path $venvPython)) {
  Write-Host "Creating $Venv"
  Invoke-Native $python -m venv $Venv
}

function Pip { Invoke-Native $venvPython -m pip --disable-pip-version-check @args }

Pip install --quiet --upgrade pip
Pip install --quiet dlib-bin face-recognition-models
Pip install --quiet --no-deps face-recognition

$filtered = Join-Path $env:TEMP "hrzest-requirements-$PID.txt"
[IO.File]::WriteAllLines($filtered, [string[]](Get-Content "$Root\requirements.txt" | Where-Object { $_ -notmatch '^\s*face-recognition\b' }))
try {
  $pipArgs = @('install', '--quiet', '-r', $filtered)
  if ($Dev) { $pipArgs += @('-r', "$Root\requirements-dev.txt") }
  Pip @pipArgs
} finally {
  Remove-Item $filtered -ErrorAction SilentlyContinue
}

Set-Content $stamp $hash -NoNewline
Write-Host "Python environment $Venv is ready."
