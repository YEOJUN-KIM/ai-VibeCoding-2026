param(
    [string]$Indicators = "KOSPI,KOSDAQ",
    [ValidateRange(1, 10000)]
    [int]$Count = 200
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    throw "Run scripts\setup-ml.ps1 before collecting data."
}

Push-Location $projectRoot
try {
    & $venvPython -m auto_trader.ml.collect_market_indicators `
        --indicators $Indicators --count $Count
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}
