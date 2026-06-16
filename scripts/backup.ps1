param(
    [string] $OutputDir = "",
    [int] $Keep = 30,
    [switch] $NoPrune
)

$ErrorActionPreference = "Stop"
Import-Module (Join-Path $PSScriptRoot "agritrack.psm1") -Force
$root = Get-AgriTrackRoot
Set-Location $root

$argsList = @("scripts/backup_agritrack.py", "--keep", "$Keep")
if ($OutputDir) {
    $argsList += @("--output-dir", $OutputDir)
}
if ($NoPrune) {
    $argsList += "--no-prune"
}

Invoke-AgriTrackPython -Arguments $argsList
$code = $LASTEXITCODE
exit $code
