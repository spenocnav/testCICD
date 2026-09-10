Set-StrictMode -Version Latest

function Resolve-ProcessStateDirectory {
    param([string]$PackageRoot, [string]$StateDirectory)

    $candidate = if ([string]::IsNullOrWhiteSpace($StateDirectory)) {
        Join-Path $PackageRoot 'runtime\processes'
    }
    elseif ([IO.Path]::IsPathRooted($StateDirectory)) { $StateDirectory }
    else { Join-Path $PackageRoot $StateDirectory }
    $resolved = [IO.Path]::GetFullPath($candidate)
    New-Item -ItemType Directory -Force -Path $resolved | Out-Null
    return $resolved
}

function Enter-ProcessControlLock {
    param([string]$StateDirectory)

    $lockPath = Join-Path $StateDirectory '.control.lock'
    try {
        return [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    }
    catch {
        throw 'Otra operación start/stop está en curso. Intente de nuevo en unos segundos.'
    }
}

function Get-ProcessMetadataPath {
    param([string]$StateDirectory, [ValidateSet('worker', 'dashboard')][string]$Role)
    return Join-Path $StateDirectory "$Role.pid.json"
}

function Get-ObjectProperty {
    param([object]$Object, [string]$Name)
    if ($null -ne $Object -and $null -ne $Object.PSObject.Properties[$Name]) {
        return $Object.PSObject.Properties[$Name].Value
    }
    return $null
}

function Get-ProcessCommandLine {
    param([int]$ProcessId)
    try {
        $instance = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
        return [string]$instance.CommandLine
    }
    catch {
        return $null
    }
}

function Get-ManagedProcessInfo {
    param([string]$StateDirectory, [ValidateSet('worker', 'dashboard')][string]$Role)

    $metadataPath = Get-ProcessMetadataPath $StateDirectory $Role
    $base = [ordered]@{
        role = $Role
        status = 'missing'
        pid = $null
        startedAt = $null
        stdoutLog = $null
        stderrLog = $null
        dashboardUrl = $null
        reason = 'Sin PID administrado'
        metadataPath = $metadataPath
    }
    if (-not (Test-Path -LiteralPath $metadataPath -PathType Leaf)) {
        return [pscustomobject]$base
    }

    try {
        $metadata = Get-Content -LiteralPath $metadataPath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
        $processId = [int](Get-ObjectProperty $metadata 'pid')
    }
    catch {
        $base.status = 'invalid_metadata'
        $base.reason = 'El PID file no es JSON válido'
        return [pscustomobject]$base
    }
    $base.pid = $processId
    $base.startedAt = Get-ObjectProperty $metadata 'processStartTimeUtc'
    $base.stdoutLog = Get-ObjectProperty $metadata 'stdoutLog'
    $base.stderrLog = Get-ObjectProperty $metadata 'stderrLog'
    $base.dashboardUrl = Get-ObjectProperty $metadata 'dashboardUrl'

    try {
        $process = Get-Process -Id $processId -ErrorAction Stop
        $actualStart = $process.StartTime.ToUniversalTime()
    }
    catch {
        $base.status = 'stale'
        $base.reason = 'El proceso ya no existe'
        return [pscustomobject]$base
    }

    try {
        $expectedStart = [DateTime]::Parse([string](Get-ObjectProperty $metadata 'processStartTimeUtc')).ToUniversalTime()
    }
    catch {
        $base.status = 'invalid_metadata'
        $base.reason = 'Falta la hora verificable de inicio'
        return [pscustomobject]$base
    }
    if ([Math]::Abs(($actualStart - $expectedStart).TotalSeconds) -gt 2) {
        $base.status = 'unverified'
        $base.reason = 'El PID fue reutilizado por otro proceso'
        return [pscustomobject]$base
    }

    $marker = [string](Get-ObjectProperty $metadata 'commandMarker')
    $commandLine = Get-ProcessCommandLine $processId
    if ([string]::IsNullOrWhiteSpace($marker) -or [string]::IsNullOrWhiteSpace($commandLine) -or
        $commandLine.IndexOf($marker, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
        $base.status = 'unverified'
        $base.reason = 'El comando del PID no coincide con el proceso administrado'
        return [pscustomobject]$base
    }

    $base.status = 'running'
    $base.reason = 'Proceso verificado'
    return [pscustomobject]$base
}

function Remove-StaleProcessMetadata {
    param([object]$Info)
    if ($Info.status -in @('stale')) {
        Remove-Item -LiteralPath $Info.metadataPath -Force -ErrorAction SilentlyContinue
    }
}

function Write-ManagedProcessMetadata {
    param(
        [string]$StateDirectory,
        [ValidateSet('worker', 'dashboard')][string]$Role,
        [System.Diagnostics.Process]$Process,
        [string]$PackageRoot,
        [string]$CommandMarker,
        [string]$StdoutLog,
        [string]$StderrLog,
        [string]$DashboardUrl
    )

    $Process.Refresh()
    $data = [ordered]@{
        schemaVersion = 1
        role = $Role
        pid = $Process.Id
        processStartTimeUtc = $Process.StartTime.ToUniversalTime().ToString('o')
        registeredAtUtc = [DateTime]::UtcNow.ToString('o')
        packageRoot = $PackageRoot
        commandMarker = $CommandMarker
        stdoutLog = $StdoutLog
        stderrLog = $StderrLog
        dashboardUrl = $DashboardUrl
    }
    $path = Get-ProcessMetadataPath $StateDirectory $Role
    $temporary = "$path.$PID.tmp"
    $utf8 = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($temporary, ($data | ConvertTo-Json -Depth 4), $utf8)
    Move-Item -LiteralPath $temporary -Destination $path -Force
}

function ConvertTo-ProcessArgument {
    param([AllowEmptyString()][string]$Value)
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }
    $builder = New-Object Text.StringBuilder
    [void]$builder.Append('"')
    $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') { $slashes++; continue }
        if ($character -eq '"') {
            [void]$builder.Append(('\' * ($slashes * 2 + 1)))
            [void]$builder.Append('"')
        }
        else {
            [void]$builder.Append(('\' * $slashes))
            [void]$builder.Append($character)
        }
        $slashes = 0
    }
    [void]$builder.Append(('\' * ($slashes * 2)))
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Start-ManagedPythonProcess {
    param(
        [string]$Python,
        [string]$Launcher,
        [string]$Script,
        [string[]]$Arguments,
        [string]$StdoutLog,
        [string]$StderrLog
    )

    if (-not (Test-Path -LiteralPath $Launcher -PathType Leaf)) {
        throw "No existe el lanzador seguro de procesos: $Launcher"
    }
    $launcherArguments = @(
        $Launcher,
        '--python', $Python,
        '--script', $Script,
        '--stdout-log', $StdoutLog,
        '--stderr-log', $StderrLog,
        '--'
    ) + @($Arguments)
    $json = & $Python @launcherArguments
    if ($LASTEXITCODE -ne 0) {
        throw "No fue posible iniciar el proceso Python administrado para $Script."
    }
    try { $spawned = $json | ConvertFrom-Json -ErrorAction Stop }
    catch { throw "El lanzador seguro devolvió una respuesta inválida para $Script." }
    $spawnedPid = [int](Get-ObjectProperty $spawned 'pid')
    if ($spawnedPid -le 0) { throw "El lanzador seguro no devolvió un PID válido para $Script." }
    return Get-Process -Id $spawnedPid -ErrorAction Stop
}

function Resolve-PythonExecutable {
    param([string]$PackageRoot, [string]$Python)
    if (-not [string]::IsNullOrWhiteSpace($Python)) { return $Python }
    $venvPython = Join-Path $PackageRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) { return $venvPython }
    return 'python'
}

function Resolve-EnvironmentFile {
    param([string]$PackageRoot, [string]$EnvFile)
    $candidate = if ([string]::IsNullOrWhiteSpace($EnvFile)) { Join-Path $PackageRoot '.env' }
    elseif ([IO.Path]::IsPathRooted($EnvFile)) { $EnvFile }
    else { Join-Path $PackageRoot $EnvFile }
    try { return (Resolve-Path -LiteralPath $candidate -ErrorAction Stop).Path }
    catch { throw "No existe el archivo de entorno: $candidate" }
}

function Set-DotEnvForChildProcesses {
    param([string]$Python, [string]$EnvFile)

    $code = 'import json,sys; from dotenv import dotenv_values; print(json.dumps({k:v for k,v in dotenv_values(sys.argv[1]).items() if v is not None}))'
    $json = & $Python -c $code $EnvFile
    if ($LASTEXITCODE -ne 0) { throw 'No fue posible leer el archivo de entorno con python-dotenv.' }
    try { $values = $json | ConvertFrom-Json -ErrorAction Stop }
    catch { throw 'El archivo de entorno no pudo convertirse a un entorno seguro.' }

    $added = New-Object System.Collections.Generic.List[string]
    foreach ($property in $values.PSObject.Properties) {
        $name = [string]$property.Name
        if ($name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { continue }
        if ($null -eq [Environment]::GetEnvironmentVariable($name, 'Process')) {
            [Environment]::SetEnvironmentVariable($name, [string]$property.Value, 'Process')
            $added.Add($name)
        }
    }
    return ,$added.ToArray()
}

function Restore-ChildProcessEnvironment {
    param([string[]]$AddedNames)
    foreach ($name in @($AddedNames)) {
        [Environment]::SetEnvironmentVariable($name, $null, 'Process')
    }
}

function Stop-VerifiedManagedProcess {
    param([string]$StateDirectory, [ValidateSet('worker', 'dashboard')][string]$Role, [int]$TimeoutSeconds = 10)

    $info = Get-ManagedProcessInfo $StateDirectory $Role
    if ($info.status -eq 'missing') { return $info }
    if ($info.status -eq 'stale') {
        Remove-StaleProcessMetadata $info
        return $info
    }
    if ($info.status -ne 'running') {
        throw "No se detuvo ${Role}: $($info.reason). Revise $($info.metadataPath)."
    }
    Stop-Process -Id $info.pid -Force -ErrorAction Stop
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ((Get-Process -Id $info.pid -ErrorAction SilentlyContinue) -and
        [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 100
    }
    if (Get-Process -Id $info.pid -ErrorAction SilentlyContinue) {
        throw "El proceso $Role ($($info.pid)) no terminó dentro de $TimeoutSeconds segundos."
    }
    Remove-Item -LiteralPath $info.metadataPath -Force -ErrorAction Stop
    return $info
}
