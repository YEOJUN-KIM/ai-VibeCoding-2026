param(
    [string]$Symbols = "",
    [ValidateRange(1, 10000)]
    [int]$Count = 200
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    throw "Run scripts\setup-ml.ps1 before collecting data."
}

$arguments = @("-m", "auto_trader.ml.collect_candles", "--count", $Count)
if ($Symbols.Trim()) {
    $arguments += @("--symbols", $Symbols)
}

Push-Location $projectRoot
try {
    & $venvPython @arguments
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}
