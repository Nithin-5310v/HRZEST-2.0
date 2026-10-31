<#
  One-time setup (safe to re-run) of Jenkins and the development copy on
  this Windows PC. No admin rights needed. See ci/jenkins/README.md.

    powershell -ExecutionPolicy Bypass -File ci\jenkins\install-jenkins.ps1 `
      -EnvSource C:\path\to\HRZEST\.env `
      -Python C:\path\to\Python312\python.exe `
      -ProdHost ubuntu@<server-ip> -ProdHostKey "SHA256:..." -ProdSshKey C:\path\to\key.ppk

  - Java 21 (Eclipse Temurin) and the Jenkins LTS war into <Root>, checksums
    verified; the plugins in plugins.txt.
  - An admin password, generated once, in <Root>\secrets\admin-password.txt.
  - <DevHome>\.env for the development copy: a copy of -EnvSource with its
    own database (employee_attendance_dev), port and plain HTTP.
  - <Root>\start-jenkins.ps1, started now and at every logon (a shortcut in
    the Startup folder); it also brings the development copy back up.
  Jenkins listens on http://localhost:<Port> only.
#>
param(
  [Parameter(Mandatory)] [string] $EnvSource,
  [Parameter(Mandatory)] [string] $Python,
  [string] $ProdHost = '',
  [string] $ProdHostKey = '',
  [string] $ProdSshKey = '',
  [string] $Plink = 'C:\Program Files\PuTTY\plink.exe',
  [string] $Root = 'C:\Jenkins',
  [string] $DevHome = 'C:\HRZEST-dev',
  [int] $Port = 8090,
  [int] $DevPort = 5050,
  [switch] $Upgrade,        # fetch the newest Java 21 / Jenkins LTS / plugins again
  [switch] $RefreshDevEnv   # rebuild <DevHome>\.env from -EnvSource
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is very slow with the progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
. "$PSScriptRoot\common.ps1"

foreach ($d in $Root, "$Root\home", "$Root\casc", "$Root\secrets", "$Root\logs", "$Root\venvs", $DevHome) {
  New-Item -ItemType Directory -Force $d | Out-Null
}

function Get-Verified([string] $Url, [string] $Sha256, [string] $Dest) {
  Write-Host "Downloading $Url"
  Invoke-WebRequest -Uri $Url -OutFile "$Dest.part" -UseBasicParsing
  $actual = (Get-FileHash "$Dest.part" -Algorithm SHA256).Hash
  if ($actual -ne $Sha256.ToUpper()) { Remove-Item "$Dest.part"; throw "Checksum mismatch for $Url" }
  Move-Item "$Dest.part" $Dest -Force
}

# ── Java 21 ──
$java = "$Root\jdk\bin\java.exe"
if ($Upgrade -or -not (Test-Path $java)) {
  $asset = (Invoke-RestMethod 'https://api.adoptium.net/v3/assets/latest/21/hotspot?architecture=x64&image_type=jdk&os=windows&vendor=eclipse')[0]
  $zip = "$Root\jdk21.zip"
  Get-Verified $asset.binary.package.link $asset.binary.package.checksum $zip
  $unpack = "$Root\jdk-unpack"
  Remove-Item $unpack, "$Root\jdk" -Recurse -Force -ErrorAction SilentlyContinue
  Expand-Archive $zip $unpack
  Move-Item (Get-ChildItem $unpack -Directory | Select-Object -First 1).FullName "$Root\jdk"
  Remove-Item $unpack, $zip -Recurse -Force
}
Write-Host "Java: $((Invoke-Native $java -version | Select-Object -First 1))"

# ── Jenkins LTS ──
$war = "$Root\jenkins.war"
if ($Upgrade -or -not (Test-Path $war)) {
  $sum = (Invoke-WebRequest 'https://get.jenkins.io/war-stable/latest/jenkins.war.sha256' -UseBasicParsing).Content
  if ($sum -is [byte[]]) { $sum = [Text.Encoding]::ASCII.GetString($sum) }
  Get-Verified 'https://get.jenkins.io/war-stable/latest/jenkins.war' ($sum.Trim() -split '\s+')[0] $war
}

# ── Plugins ──
$pluginDir = "$Root\home\plugins"
if ($Upgrade -or -not (Test-Path "$pluginDir\configuration-as-code.jpi")) {
  $release = Invoke-RestMethod 'https://api.github.com/repos/jenkinsci/plugin-installation-manager-tool/releases/latest'
  $jar = $release.assets | Where-Object { $_.name -like 'jenkins-plugin-manager-*.jar' } | Select-Object -First 1
  $pm = "$Root\jenkins-plugin-manager.jar"
  Write-Host "Downloading $($jar.name)"
  Invoke-WebRequest $jar.browser_download_url -OutFile $pm -UseBasicParsing
  Invoke-Native $java -jar $pm --war $war --plugin-download-directory $pluginDir --plugin-file "$PSScriptRoot\plugins.txt"
}

# ── Admin password (generated once, readable only by this Windows user) ──
$passwordFile = "$Root\secrets\admin-password.txt"
if (-not (Test-Path $passwordFile)) {
  $chars = [char[]]'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789'
  $bytes = New-Object byte[] 24
  [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  Set-Content $passwordFile (-join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })) -NoNewline
}
Invoke-Native icacls "$Root\secrets" /inheritance:r /grant:r "${env:USERNAME}:(OI)(CI)F" | Out-Null

# ── The development copy's .env ──
$devEnv = "$DevHome\.env"
if ($RefreshDevEnv -or -not (Test-Path $devEnv)) {
  $overrides = [ordered]@{
    DB_NAME       = 'employee_attendance_dev'
    PORT          = "$DevPort"
    APP_ENV       = 'development'
    # No certificate there, so run_dev.py serves plain HTTP.
    SSL_CERT_PATH = "$DevHome\no-cert.pem"
    SSL_KEY_PATH  = "$DevHome\no-key.pem"
  }
  $lines = Get-Content $EnvSource | Where-Object {
    -not ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=' -and $overrides.Contains($matches[1]))
  }
  $lines += '', '# Development copy (ci/jenkins/install-jenkins.ps1)'
  $lines += $overrides.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }
  # UTF-8 without a BOM: python-dotenv would read a BOM as part of the first key.
  [IO.File]::WriteAllLines($devEnv, [string[]]$lines, (New-Object Text.UTF8Encoding $false))
  Write-Host "Wrote $devEnv (database employee_attendance_dev, port $DevPort)"
}
Invoke-Native icacls $devEnv /inheritance:r /grant:r "${env:USERNAME}:F" | Out-Null

# ── Configuration as code ──
Copy-Item "$PSScriptRoot\jenkins.yaml" "$Root\casc\jenkins.yaml" -Force

# ── Start script ──
$start = "$Root\start-jenkins.ps1"
@"
# Generated by ci/jenkins/install-jenkins.ps1 -- re-run that to change it.
`$ErrorActionPreference = 'Stop'
`$env:JENKINS_HOME = '$Root\home'
`$env:JENKINS_PORT = '$Port'
`$env:JENKINS_ADMIN_PASSWORD_FILE = '$passwordFile'
`$env:HRZEST_CI_HOME = '$Root'
`$env:HRZEST_DEV_HOME = '$DevHome'
`$env:HRZEST_PYTHON = '$Python'
`$env:HRZEST_PLINK = '$Plink'
`$env:HRZEST_PROD_HOST = '$ProdHost'
`$env:HRZEST_PROD_HOSTKEY = '$ProdHostKey'
`$env:HRZEST_PROD_SSH_KEY = '$ProdSshKey'

# Bring the development copy back up after a reboot.
if (Test-Path '$DevHome\start_dev_server.ps1') {
  & '$DevHome\start_dev_server.ps1' -DevHome '$DevHome' -OnlyIfStopped *>> '$Root\logs\start.log'
}

if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) { exit 0 }
Start-Process -FilePath '$Root\jdk\bin\java.exe' -WindowStyle Hidden ``
  -RedirectStandardOutput '$Root\logs\jenkins.out.log' -RedirectStandardError '$Root\logs\jenkins.log' ``
  -ArgumentList @('-Xmx1g', '-Dfile.encoding=UTF-8', '-Djenkins.install.runSetupWizard=false',
                  '-Dcasc.jenkins.config=$Root\casc\jenkins.yaml',
                  '-jar', '$war', '--httpPort=$Port', '--httpListenAddress=127.0.0.1')
"@ | Set-Content $start -Encoding utf8

# ── Start at logon ──
$shortcut = Join-Path ([Environment]::GetFolderPath('Startup')) 'HRZEST Jenkins.lnk'
$link = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcut)
$link.TargetPath = "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe"
$link.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$start`""
$link.WindowStyle = 7
$link.Save()

# ── Start now ──
# As its own process, like the logon shortcut: run as a child, Jenkins would
# inherit this script's output handles and keep a caller's pipe open.
Start-Process powershell -WindowStyle Hidden -ArgumentList @('-NoProfile', '-WindowStyle', 'Hidden', '-ExecutionPolicy', 'Bypass', '-File', $start)
Write-Host "Waiting for Jenkins on http://localhost:$Port ..."
$deadline = (Get-Date).AddMinutes(5)
while ((Get-HttpStatus "http://localhost:$Port/login") -ne 200) {
  if ((Get-Date) -gt $deadline) { throw "Jenkins did not start -- see $Root\logs\jenkins.log" }
  Start-Sleep -Seconds 5
}
Write-Host ''
Write-Host "Jenkins is running: http://localhost:$Port  (user: admin, password in $passwordFile)"
