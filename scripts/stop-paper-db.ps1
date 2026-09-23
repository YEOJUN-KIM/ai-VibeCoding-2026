param([string]$PostgresBin = 'C:\Program Files\PostgreSQL\18\bin')
$dataDir = Join-Path (Split-Path $PSScriptRoot -Parent) '.paper-postgres'
& (Join-Path $PostgresBin 'pg_ctl.exe') -D $dataDir -m fast -w stop
exit $LASTEXITCODE
