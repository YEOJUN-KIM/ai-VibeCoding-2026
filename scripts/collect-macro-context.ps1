param(
    [ValidateRange(1, 3650)]
    [int]$Days = 14
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    throw "Run scripts\setup-ml.ps1 before collecting data."
}

Push-Location $projectRoot
try {
    & $venvPython -m auto_trader.ml.macro_pipeline --days $Days
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}
