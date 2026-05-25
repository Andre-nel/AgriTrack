$ErrorActionPreference = "Stop"
Import-Module (Join-Path $PSScriptRoot "agritrack.psm1") -Force
$root = Get-AgriTrackRoot
Set-Location $root

$code = Invoke-AgriTrackPython -Arguments @("-m", "pytest")
exit $code
