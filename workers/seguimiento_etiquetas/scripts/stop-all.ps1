[CmdletBinding()]
param(
    [string]$StateDirectory
)

$ErrorActionPreference = 'Stop'
$PackageRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'process-control.ps1')
$StateRoot = Resolve-ProcessStateDirectory $PackageRoot $StateDirectory
$ControlLock = Enter-ProcessControlLock $StateRoot
$failed = $false
try {
    foreach ($role in @('dashboard', 'worker')) {
        try {
            $info = Stop-VerifiedManagedProcess $StateRoot $role
            if ($info.status -eq 'running') { Write-Output "$role detenido (PID $($info.pid))." }
            elseif ($info.status -eq 'stale') { Write-Output "$role ya estaba detenido; PID obsoleto eliminado." }
            else { Write-Output "$role no tenía un proceso administrado." }
        }
        catch {
            $failed = $true
            Write-Error $_.Exception.Message
        }
    }
}
finally {
    $ControlLock.Dispose()
}
if ($failed) { exit 1 }
