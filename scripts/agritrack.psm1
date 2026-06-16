function Get-AgriTrackRoot {
    return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
}

function Invoke-AgriTrackPython {
    param(
        [Parameter(Mandatory = $true)]
        [string[]] $Arguments
    )

    $root = Get-AgriTrackRoot
    $active = $null
    if ($env:VIRTUAL_ENV) {
        $active = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
    }

    if ($active -and (Test-Path -LiteralPath $active)) {
        & $active @Arguments
        return
    }

    $preferred = Join-Path $root ".venv\Scripts\python.exe"

    if (Test-Path -LiteralPath $preferred) {
        & $preferred @Arguments
        return
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        & $python.Source @Arguments
        return
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        & $py.Source -3.12 @Arguments
        return
    }

    throw "No Python interpreter found. Install Python 3.12, then run scripts\setup-venv.ps1."
}

Export-ModuleMember -Function Get-AgriTrackRoot, Invoke-AgriTrackPython
