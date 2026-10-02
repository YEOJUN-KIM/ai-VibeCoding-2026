$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$requirements = Join-Path $projectRoot "requirements-ml.txt"

if (-not (Test-Path $venvPython)) {
    Write-Host "[1/3] Creating the .venv environment."
    python -m venv (Join-Path $projectRoot ".venv")
}

Write-Host "[2/3] Installing application and ML packages."
& $venvPython -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[3/3] Training, saving, and reloading the example model."
Push-Location $projectRoot
try {
    & $venvPython -m auto_trader.ml.verify_environment
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}
