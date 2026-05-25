function Get-AgriTrackRoot {
    return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
}

function Invoke-AgriTrackPython {
    param(
        [Parameter(Mandatory = $true)]
        [string[]] $Arguments
    )

    $root = Get-AgriTrackRoot
    $preferred = Join-Path $root ".venv\Scripts\python.exe"

    if (Test-Path -LiteralPath $preferred) {
        & $preferred @Arguments
        return $LASTEXITCODE
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        & $python.Source @Arguments
        return $LASTEXITCODE
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        & $py.Source -3.12 @Arguments
        return $LASTEXITCODE
    }

    throw "No Python interpreter found. Install Python 3.12, then run scripts\setup-venv.ps1."
}

Export-ModuleMember -Function Get-AgriTrackRoot, Invoke-AgriTrackPython
