[CmdletBinding()]
param(
    [int]$Port = 8765,
    [string]$HostAddress = '127.0.0.1'
)

$ErrorActionPreference = 'Stop'
$PackageRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$VenvPython = Join-Path $PackageRoot '.venv\Scripts\python.exe'
$Python = if (Test-Path -LiteralPath $VenvPython) { $VenvPython } else { 'python' }

Push-Location -LiteralPath $PackageRoot
try {
    & $Python -u (Join-Path $PackageRoot 'scripts\serve_dashboard.py') --host $HostAddress --port $Port
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}

