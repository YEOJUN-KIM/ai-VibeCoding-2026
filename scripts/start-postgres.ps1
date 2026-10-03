param([Parameter(Mandatory=$true)][string]$ServiceName, [switch]$Elevated)
$ErrorActionPreference = 'Stop'
try {
    if ($ServiceName -notmatch '^postgresql[-a-zA-Z0-9_]*$') { throw 'Invalid PostgreSQL service name.' }
    $service = Get-Service -Name $ServiceName -ErrorAction Stop
    if ($service.Status -eq 'Running') { exit 0 }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        if ($Elevated) { throw 'Administrator permission was not granted.' }
        $taskArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $PSCommandPath + '"'), '-ServiceName', $ServiceName, '-Elevated')
        $taskProcess = Start-Process -FilePath 'powershell.exe' -Verb RunAs -WindowStyle Hidden -Wait -PassThru -ArgumentList $taskArgs
        exit $taskProcess.ExitCode
    }
    Start-Service -Name $ServiceName
    (Get-Service -Name $ServiceName).WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
    exit 0
} catch {
    Write-Host 'Could not start PostgreSQL. Check Windows Services or administrator permission.'
    exit 1
}
