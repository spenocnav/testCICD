<#
.SYNOPSIS
  Despliegue del Portal Clientes en Docker Desktop (ensayo local con CI/CD).

.DESCRIPTION
  Ejecuta la misma secuencia que correrá en la VM de Azure:
    validar -> construir -> levantar -> comprobar salud
  Apila tres archivos de compose y NO modifica ninguno de ellos.

  Perfiles:
    core  (por defecto) postgres, redis, minio, migraciones, api, web y los dos
          workers que no salen a Internet. Es lo que se valida en el ensayo.
    full  añade los workers de integración y el ETL. Sin claves externas
          entran en bucle de reinicio: usar sólo para probar que las imágenes
          construyen.

.EXAMPLE
  .\deploy\deploy-local.ps1
  .\deploy\deploy-local.ps1 -Stack full -SkipBuild
#>
[CmdletBinding()]
param(
  [ValidateSet('core', 'full')][string]$Stack = 'core',
  [string]$EnvFile = 'deploy/production.env',
  [switch]$SkipBuild,
  [int]$HealthTimeoutSeconds = 300
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$files = @(
  '-f', 'docker-compose.yml',
  '-f', 'docker-compose.prod.yml',
  '-f', 'deploy/compose.ops.yml'
)
$base = @('compose', '--env-file', $EnvFile) + $files + @('--profile', 'full')

# Servicios del perfil `core`. `migrate` es one-shot y `api` depende de que
# termine bien, así que no hace falta nombrarlo aparte.
$coreServices = @(
  'postgres', 'redis', 'minio', 'minio-init', 'migrate',
  'api', 'web', 'novedad-worker', 'auth-housekeeping'
)

function Step($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }
function Fail($text) { Write-Host "FALLO: $text" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------- pre-vuelo
Step 'Pre-vuelo'
if (-not (Test-Path $EnvFile)) {
  Fail "no existe $EnvFile. Copiar deploy/ensayo-local.env.example y rellenarlo."
}
if ((Get-Content $EnvFile -Raw) -match 'REPLACE_') {
  Fail "$EnvFile todavía contiene marcadores REPLACE_*."
}
docker network inspect navi-red-central *> $null
if ($LASTEXITCODE -ne 0) { Fail "falta la red externa. Ejecutar: docker network create navi-red-central" }

# Esta máquina aloja otras pilas. El overlay de producción sólo publica el
# puerto del frontend (postgres, redis, minio y api hacen `ports: !reset []`),
# pero se comprueba igualmente para no pisar nada ajeno.
$webPort = 3000
$envWebPort = (Select-String -Path $EnvFile -Pattern '^WEB_PORT=(\d+)' -ErrorAction SilentlyContinue).Matches.Groups[1].Value
if ($envWebPort) { $webPort = [int]$envWebPort }
$owner = docker ps -a --filter "publish=$webPort" --format '{{.Names}}'
$foreign = $owner | Where-Object { $_ -and $_ -notlike 'portal-cliente-*' }
if ($foreign) { Fail "el puerto $webPort ya lo publica un contenedor ajeno: $($foreign -join ', '). Cambiar WEB_PORT en $EnvFile." }
if (-not $owner -and (Get-NetTCPConnection -LocalPort $webPort -State Listen -ErrorAction SilentlyContinue)) {
  Fail "el puerto $webPort está ocupado por un proceso del host. Cambiar WEB_PORT en $EnvFile."
}
Write-Host "ok: entorno, secretos, red y puerto $webPort" -ForegroundColor Green

# ------------------------------------------------------------------ validar
Step 'Validar composición'
docker @base config *> $null
if ($LASTEXITCODE -ne 0) { docker @base config; Fail 'la composición no es válida' }
Write-Host 'ok: los tres archivos de compose resuelven' -ForegroundColor Green

# ---------------------------------------------------------------- construir
if (-not $SkipBuild) {
  Step "Construir imágenes (perfil $Stack)"
  Write-Host 'La primera vez tarda 20-40 min (pnpm install + next build + pip).' -ForegroundColor Yellow
  if ($Stack -eq 'core') { docker @base build $coreServices } else { docker @base build }
  if ($LASTEXITCODE -ne 0) { Fail 'la construcción falló' }
}

# ---------------------------------------------------------------- levantar
Step "Levantar servicios (perfil $Stack)"
# SIN --remove-orphans a propósito: esta máquina aloja otras pilas (ruterox,
# anylist, ...). El flag sólo borra contenedores del MISMO proyecto de compose,
# pero no se usa para no depender de esa garantía.
if ($Stack -eq 'core') {
  docker @base up -d $coreServices
}
else {
  docker @base up -d
}
if ($LASTEXITCODE -ne 0) { Fail 'el arranque falló' }

# ------------------------------------------------------------ comprobar salud
Step 'Comprobar salud'
$deadline = (Get-Date).AddSeconds($HealthTimeoutSeconds)
$targets = @('portal-cliente-api', 'portal-cliente-web')
do {
  $pending = @()
  foreach ($c in $targets) {
    $state = docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' $c 2>$null
    if ($state -ne 'healthy' -and $state -ne 'running') { $pending += "$c=$state" }
  }
  if ($pending.Count -eq 0) { break }
  Write-Host "  esperando: $($pending -join ', ')"
  Start-Sleep -Seconds 10
} while ((Get-Date) -lt $deadline)

if ($pending.Count -gt 0) {
  Write-Host "`n--- diagnóstico ---" -ForegroundColor Yellow
  docker @base ps
  docker @base logs --tail 60 api
  Fail "servicios no saludables tras $HealthTimeoutSeconds s: $($pending -join ', ')"
}

# La API no publica puerto en el overlay de producción: se comprueba por dentro.
docker exec portal-cliente-api curl -fsS http://localhost:8000/health/ready *> $null
if ($LASTEXITCODE -ne 0) { docker @base logs --tail 40 api; Fail '/health/ready no responde 200' }

try { Invoke-WebRequest -Uri 'http://localhost:3000/login' -UseBasicParsing -TimeoutSec 20 | Out-Null }
catch { Fail "el frontend no responde en http://localhost:3000/login : $_" }

Step 'Despliegue correcto'
docker @base ps --format 'table {{.Service}}\t{{.Status}}'
Write-Host "`nPortal: http://localhost:3000" -ForegroundColor Green
Write-Host 'Sin rollback automático: requiere registro de imágenes (fase B7).' -ForegroundColor DarkGray
exit 0
