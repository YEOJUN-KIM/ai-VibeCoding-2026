param(
    [string]$Start = "",
    [string]$End = "",
    [int]$StrategyId = 0,
    [string]$Symbols = "",
    [ValidateSet("next_open", "close")]
    [string]$Execution = "next_open"
)

$ErrorActionPreference = "Stop"
$taskProjectRoot = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $taskProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $taskPython)) { throw "Project .venv is missing." }
$taskStamp = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTime]::UtcNow, "Korea Standard Time").ToString("yyyyMMdd-HHmmss")
$taskRunSuffix = [Guid]::NewGuid().ToString("N").Substring(0, 8)
$taskOutput = "artifacts/ml/generated/backtest-$taskStamp-$taskRunSuffix"
$taskArguments = @("-m", "auto_trader.ml.backtest", "--execution", $Execution, "--output", $taskOutput)
if ($Start) { $taskArguments += @("--start", $Start) }
if ($End) { $taskArguments += @("--end", $End) }
if ($StrategyId) { $taskArguments += @("--strategy-id", "$StrategyId") }
if ($Symbols) { $taskArguments += @("--symbols", $Symbols) }
Push-Location $taskProjectRoot
try {
    & $taskPython @taskArguments
    if ($LASTEXITCODE -ne 0) { throw "Backtest failed with exit code $LASTEXITCODE" }
    Write-Output "Results: $taskProjectRoot\$taskOutput\summary.md"
}
finally { Pop-Location }
