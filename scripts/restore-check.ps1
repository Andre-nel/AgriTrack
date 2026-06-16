param(
    [string] $Backup = "",
    [string] $BackupDir = ""
)

$ErrorActionPreference = "Stop"
Import-Module (Join-Path $PSScriptRoot "agritrack.psm1") -Force
$root = Get-AgriTrackRoot
Set-Location $root

$argsList = @("scripts/restore_check.py")
if ($Backup) {
    $argsList += $Backup
}
if ($BackupDir) {
    $argsList += @("--backup-dir", $BackupDir)
}

Invoke-AgriTrackPython -Arguments $argsList
$code = $LASTEXITCODE
exit $code
