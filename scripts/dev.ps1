param(
    [int] $Port = 5000,
    [switch] $SkipMigrations
)

$ErrorActionPreference = "Stop"
Import-Module (Join-Path $PSScriptRoot "agritrack.psm1") -Force
$root = Get-AgriTrackRoot
Set-Location $root

$env:FLASK_APP = "manage.py"
if (-not $env:FLASK_ENV) { $env:FLASK_ENV = "development" }
if (-not $env:DATABASE_URL) { $env:DATABASE_URL = "sqlite:///agritrack.db" }
if (-not $env:SECRET_KEY) { $env:SECRET_KEY = "dev-secret-key" }

if (-not $SkipMigrations) {
    Invoke-AgriTrackPython -Arguments @("-m", "flask", "db", "upgrade")
    $code = $LASTEXITCODE
    if ($code -ne 0) { exit $code }
}

Invoke-AgriTrackPython -Arguments @("-m", "flask", "run", "--host", "127.0.0.1", "--port", "$Port")
$code = $LASTEXITCODE
exit $code
