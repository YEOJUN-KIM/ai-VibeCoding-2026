param([string]$PostgresBin = 'C:\Program Files\PostgreSQL\18\bin')
$ErrorActionPreference = 'Stop'
$projectDir = Split-Path $PSScriptRoot -Parent
$dataDir = Join-Path $projectDir '.paper-postgres'
if (-not (Test-Path (Join-Path $dataDir 'PG_VERSION'))) {
    throw 'Local database is missing. See docs/DATABASE.md for PostgreSQL setup.'
}
& (Join-Path $PostgresBin 'pg_ctl.exe') -D $dataDir status
if ($LASTEXITCODE -eq 0) { exit 0 }
Start-Process -FilePath (Join-Path $PostgresBin 'postgres.exe') -ArgumentList @('-D', ('"' + $dataDir + '"'), '-p', '55432', '-h', '127.0.0.1') -WindowStyle Hidden -RedirectStandardError (Join-Path $dataDir 'server.log')
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    & (Join-Path $PostgresBin 'pg_isready.exe') -h 127.0.0.1 -p 55432
    if ($LASTEXITCODE -eq 0) { exit 0 }
    Start-Sleep -Milliseconds 500
}
throw 'PostgreSQL did not start. Check .paper-postgres/server.log.'
