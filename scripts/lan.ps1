param(
    [int] $Port = 5000,
    [string] $HostAddress = "0.0.0.0",
    [switch] $SkipMigrations
)

$ErrorActionPreference = "Stop"
Import-Module (Join-Path $PSScriptRoot "agritrack.psm1") -Force
$root = Get-AgriTrackRoot
Set-Location $root

if (-not $env:SECRET_KEY -or $env:SECRET_KEY -eq "dev-secret-key") {
    throw "Set SECRET_KEY to a long private value before starting LAN mode."
}

$env:FLASK_APP = "manage.py"
$env:FLASK_ENV = "production"
if (-not $env:DATABASE_URL) { $env:DATABASE_URL = "sqlite:///agritrack.db" }
$env:LAN_HOST = $HostAddress
$env:PORT = "$Port"

if (-not $SkipMigrations) {
    Invoke-AgriTrackPython -Arguments @("-m", "flask", "db", "upgrade")
    $code = $LASTEXITCODE
    if ($code -ne 0) { exit $code }
}

Invoke-AgriTrackPython -Arguments @("scripts/serve_lan.py")
$code = $LASTEXITCODE
if ($code -eq 0) {
    Write-Warning "AgriTrack LAN server exited. It should normally keep running until you stop it."
}
exit $code
