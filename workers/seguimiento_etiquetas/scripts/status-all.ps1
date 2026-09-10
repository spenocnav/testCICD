[CmdletBinding()]
param(
    [string]$StateDirectory,
    [switch]$Json
)

$ErrorActionPreference = 'Stop'
$PackageRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'process-control.ps1')
$StateRoot = Resolve-ProcessStateDirectory $PackageRoot $StateDirectory
$items = @('worker', 'dashboard') | ForEach-Object { Get-ManagedProcessInfo $StateRoot $_ }

if ($Json) {
    $items | Select-Object role, status, pid, startedAt, dashboardUrl, stdoutLog, stderrLog, reason | ConvertTo-Json -Depth 3
}
else {
    foreach ($item in $items) {
        $pidText = if ($null -eq $item.pid) { '-' } else { [string]$item.pid }
        Write-Output ("{0,-10} {1,-18} PID {2,-7} {3}" -f $item.role, $item.status, $pidText, $item.reason)
        if ($item.dashboardUrl) { Write-Output "  URL: $($item.dashboardUrl)" }
        if ($item.stdoutLog) { Write-Output "  stdout: $($item.stdoutLog)" }
        if ($item.stderrLog) { Write-Output "  stderr: $($item.stderrLog)" }
    }
}
if (@($items | Where-Object { $_.status -ne 'running' }).Count -gt 0) { exit 1 }
