$ErrorActionPreference = "Stop"
Import-Module (Join-Path $PSScriptRoot "agritrack.psm1") -Force
$root = Get-AgriTrackRoot
Set-Location $root

Invoke-AgriTrackPython -Arguments @("-m", "pytest")
$code = $LASTEXITCODE
exit $code
