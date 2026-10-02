param(
    [string]$Date = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    throw "Run scripts\setup-ml.ps1 before generating reports."
}

$arguments = @("-m", "auto_trader.ml.quality_report")
if ($Date.Trim()) {
    $arguments += @("--date", $Date)
}

Push-Location $projectRoot
try {
    & $venvPython @arguments
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}
