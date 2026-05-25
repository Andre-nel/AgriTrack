param(
    [switch] $Recreate
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$venv = Join-Path $root ".venv"
$venvPython = Join-Path $venv "Scripts\python.exe"

Set-Location $root

if ($Recreate -and (Test-Path -LiteralPath $venv)) {
    $resolved = (Resolve-Path -LiteralPath $venv).Path
    if (-not ($resolved.StartsWith($root + [System.IO.Path]::DirectorySeparatorChar))) {
        throw "Refusing to remove outside workspace: $resolved"
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        & $py.Source -3.12 -m venv .venv
    } else {
        & python -m venv .venv
    }
}

& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements/dev.txt -r requirements/prod.txt

Write-Host "Ready: $venvPython"
