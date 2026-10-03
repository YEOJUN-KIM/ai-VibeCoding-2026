param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$env:PYTHONUTF8 = '1'
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
try {
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Write-Host 'Preparing Python environment (first run only)...'
        if (Get-Command py.exe -ErrorAction SilentlyContinue) {
            & py.exe -3 -m venv (Join-Path $projectRoot '.venv')
        } elseif (Get-Command python.exe -ErrorAction SilentlyContinue) {
            & python.exe -m venv (Join-Path $projectRoot '.venv')
        } else {
            throw 'Python is missing. Install Python 3.12 or newer and run FOLIO.cmd again. See docs/guides/INSTALL_WINDOWS.md.'
        }
        if ($LASTEXITCODE -ne 0) { throw 'Could not create .venv. Check the Python installation.' }
    }
    & $venvPython -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'
    if ($LASTEXITCODE -ne 0) { throw 'This project requires Python 3.12 or newer. Check .venv and docs/guides/INSTALL_WINDOWS.md.' }
    & $venvPython (Join-Path $PSScriptRoot 'check-requirements.py')
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'Installing application packages...'
        & $venvPython -m pip install -r (Join-Path $projectRoot 'requirements.txt')
        if ($LASTEXITCODE -ne 0) { throw 'Package installation failed. Check the internet connection and try again.' }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot '.env'))) {
        Copy-Item -LiteralPath (Join-Path $projectRoot '.env.example') -Destination (Join-Path $projectRoot '.env')
        Write-Host 'Created .env. Set the PostgreSQL connection values, then run FOLIO.cmd again.'
        Write-Host 'First-time database creation: docs/guides/INSTALL_WINDOWS.md (step 3). Broker API keys can be added on the website.'
        exit 1
    }
    if ($NoBrowser) {
        & $venvPython -m auto_trader.launcher --no-browser
    } else {
        & $venvPython -m auto_trader.launcher
    }
    exit $LASTEXITCODE
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
