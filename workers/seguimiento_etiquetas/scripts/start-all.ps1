[CmdletBinding()]
param(
    [string]$EnvFile,
    [string]$Python,
    [string]$HostAddress,
    [ValidateRange(0, 65535)][int]$Port = 0,
    [string[]]$WorkerArguments = @(),
    [string]$StateDirectory,
    [switch]$NoOpenBrowser
)

$ErrorActionPreference = 'Stop'
$PackageRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'process-control.ps1')
$StateRoot = Resolve-ProcessStateDirectory $PackageRoot $StateDirectory
$ControlLock = Enter-ProcessControlLock $StateRoot
$startedRoles = New-Object System.Collections.Generic.List[string]
$addedEnvironment = @()

try {
    $states = @{}
    foreach ($role in @('worker', 'dashboard')) {
        $info = Get-ManagedProcessInfo $StateRoot $role
        if ($info.status -eq 'stale') {
            Remove-StaleProcessMetadata $info
            $info = Get-ManagedProcessInfo $StateRoot $role
        }
        if ($info.status -in @('unverified', 'invalid_metadata')) {
            throw "No se puede iniciar: $role tiene estado $($info.status). $($info.reason)."
        }
        $states[$role] = $info
    }
    if ($states.worker.status -eq 'running' -and $states.dashboard.status -eq 'running') {
        Write-Output "Worker y dashboard ya están activos. Dashboard: $($states.dashboard.dashboardUrl)"
        exit 0
    }

    foreach ($forbidden in @('--once', '--healthcheck', '--bootstrap-all-tracking', '--seed-only', '--env-file')) {
        if ($WorkerArguments -contains $forbidden) {
            throw "WorkerArguments no admite $forbidden; start-all inicia únicamente el worker continuo."
        }
    }

    $PythonExecutable = Resolve-PythonExecutable $PackageRoot $Python
    $EnvironmentPath = Resolve-EnvironmentFile $PackageRoot $EnvFile
    $addedEnvironment = @(Set-DotEnvForChildProcesses $PythonExecutable $EnvironmentPath)
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable('CLOUDFLEET_API_KEY', 'Process'))) {
        throw 'CLOUDFLEET_API_KEY no está configurada en el entorno ni en el archivo indicado.'
    }

    $resolvedHost = if (-not [string]::IsNullOrWhiteSpace($HostAddress)) { $HostAddress }
        elseif (-not [string]::IsNullOrWhiteSpace($env:DASHBOARD_HOST)) { $env:DASHBOARD_HOST }
        else { '127.0.0.1' }
    $resolvedPort = if ($Port -gt 0) { $Port }
        elseif (-not [string]::IsNullOrWhiteSpace($env:DASHBOARD_PORT)) { [int]$env:DASHBOARD_PORT }
        else { 8765 }
    if ($resolvedPort -lt 1 -or $resolvedPort -gt 65535) { throw 'DASHBOARD_PORT debe estar entre 1 y 65535.' }

    $LogsRoot = Join-Path $PackageRoot 'logs'
    New-Item -ItemType Directory -Force -Path $LogsRoot | Out-Null
    $ProcessLauncher = Join-Path $PSScriptRoot 'spawn_detached.py'
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
    $DashboardUrl = "http://$resolvedHost`:$resolvedPort/dashboard/"

    if ($states.worker.status -ne 'running') {
        $workerScript = Join-Path $PackageRoot 'tracking_label_worker.py'
        $workerOut = Join-Path $LogsRoot "worker.$stamp.jsonl"
        $workerErr = Join-Path $LogsRoot "worker.$stamp.stderr.log"
        $worker = Start-ManagedPythonProcess $PythonExecutable $ProcessLauncher $workerScript (@('--env-file', $EnvironmentPath) + $WorkerArguments) $workerOut $workerErr
        Write-ManagedProcessMetadata $StateRoot 'worker' $worker $PackageRoot $workerScript $workerOut $workerErr $null
        $startedRoles.Add('worker')
    }

    if ($states.dashboard.status -ne 'running') {
        $dashboardScript = Join-Path $PackageRoot 'scripts\serve_dashboard.py'
        $dashboardOut = Join-Path $LogsRoot "dashboard.$stamp.stdout.log"
        $dashboardErr = Join-Path $LogsRoot "dashboard.$stamp.stderr.log"
        $dashboard = Start-ManagedPythonProcess $PythonExecutable $ProcessLauncher $dashboardScript @('--host', $resolvedHost, '--port', [string]$resolvedPort) $dashboardOut $dashboardErr
        Write-ManagedProcessMetadata $StateRoot 'dashboard' $dashboard $PackageRoot $dashboardScript $dashboardOut $dashboardErr $DashboardUrl
        $startedRoles.Add('dashboard')
    }

    Start-Sleep -Milliseconds 1200
    foreach ($role in @('worker', 'dashboard')) {
        $info = Get-ManagedProcessInfo $StateRoot $role
        if ($info.status -ne 'running') {
            throw "$role no permaneció activo. Revise sus logs en $LogsRoot."
        }
    }
    Write-Output "Servicios iniciados. Dashboard: $DashboardUrl"
    Write-Output "Estado: .\scripts\status-all.ps1   Detener: .\scripts\stop-all.ps1"
    if (-not $NoOpenBrowser) {
        try { Start-Process $DashboardUrl | Out-Null }
        catch { Write-Warning "No se pudo abrir el navegador automáticamente. Abra $DashboardUrl" }
    }
}
catch {
    foreach ($role in @($startedRoles.ToArray()) | Select-Object -Reverse) {
        try { Stop-VerifiedManagedProcess $StateRoot $role | Out-Null }
        catch { Write-Warning "Rollback incompleto para ${role}: $($_.Exception.Message)" }
    }
    throw
}
finally {
    Restore-ChildProcessEnvironment $addedEnvironment
    $ControlLock.Dispose()
}
