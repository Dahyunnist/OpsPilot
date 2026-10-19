$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    if ((python -c "import sys; print(sys.version_info >= (3, 11))") -ne 'True') {
        throw 'Python 3.11 or newer is required.'
    }
    $python = Join-Path $root '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        python -m venv .venv
    }
    & $python -m pip install -r requirements.txt
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
