[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$WorkerArguments
)

$ErrorActionPreference = 'Stop'
$PackageRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$VenvPython = Join-Path $PackageRoot '.venv\Scripts\python.exe'
$Python = if (Test-Path -LiteralPath $VenvPython) { $VenvPython } else { 'python' }

Push-Location -LiteralPath $PackageRoot
try {
    & $Python -u (Join-Path $PackageRoot 'tracking_label_worker.py') @WorkerArguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}

