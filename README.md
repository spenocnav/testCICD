# Portal Clientes

Sistema bidireccional de mantenimiento predictivo. Monorepo Turborepo con frontend Next.js y backend FastAPI.

## Prerrequisitos

- Node.js >= 20
- pnpm >= 9 (`corepack enable` o instalador oficial: `iwr https://get.pnpm.io/install.ps1 -UseBasicParsing | iex`)
- Python >= 3.12
- Docker Desktop

> **Windows:** evita poner el proyecto bajo OneDrive sincronizado si vas a tener `node_modules` grande. Si tienes Postgres nativo en el host, `docker-compose.yml` mapea Postgres a `:5433` para no chocar con `:5432`.

## Estructura

```
apps/
  web/      Next.js 15 (App Router, TypeScript estricto)
  api/      FastAPI (Python 3.12, SQLAlchemy async, Alembic)
packages/
  shared-types/   Tipos TS generados desde OpenAPI (Fase 6+)
  ui/             Componentes shadcn compartidos (futuro)
InformesRendimiento/
  extract/ transform/ load/ streaming/   ETL de reportes (MyGeotab -> esquema analytics)
  worker.py                             Proceso que atiende los disparos y el flujo de fallas
Docs/
  DATABASE_ARCHITECTURE.md                        Esquemas public y analytics
  Arquitectura y Stack Tecnológico - Navifault.txt  Doc de referencia externa
  styles.md
  adr/                                            Architecture Decision Records
```

`ARQUITECTURA_COMPLETA.md` describe los módulos, los flujos y el estado del
proyecto; `CONTRIBUTING.md`, cómo aportar cambios.

## Setup inicial

```powershell
# 1. Variables
cp .env.example .env

cp InformesRendimiento/.env.example InformesRendimiento/.env   # ETL de reportes (misma MASTER_FERNET_KEY que la API)
# 2. Workspace
pnpm install

# 3. Infraestructura
docker compose up -d postgres redis

# 4. Backend (venv + deps + migraciones)
cd apps\api
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m alembic upgrade head
cd ..\..
```

Credenciales bootstrap (definidas en `.env`):

- `admin@portalclientes.local`
- `ChangeMe123!` (cámbialas inmediatamente en cualquier entorno serio)

## Desarrollo

```powershell
docker compose up -d postgres redis     # infra
pnpm dev                                # web + api en paralelo
pnpm --filter web dev                   # solo web
pnpm --filter api dev                   # solo api (requiere venv activado)
```

URLs:

- Web: <http://localhost:3000>
- API: <http://localhost:8000>
- Docs API (Swagger): <http://localhost:8000/docs>
- Health: <http://localhost:8000/health>
- Liveness (solo proceso): <http://localhost:8000/health/live>
- Readiness (DB + Redis): <http://localhost:8000/health/ready>

## Comandos útiles

| Comando             | Acción                                                                    |
| ------------------- | ------------------------------------------------------------------------- |
| `pnpm dev`          | Levanta web + api en paralelo                                             |
| `pnpm build`        | Build de todos los paquetes                                               |
| `pnpm lint`         | Lint en todos los workspaces                                              |
| `pnpm typecheck`    | TypeScript estricto + mypy                                                |
| `pnpm test`         | Tests unitarios e integración (FE Vitest + BE pytest)                     |
| `pnpm check`        | Equivalente local a CI: `lint + typecheck + test + build` en todo el repo |
| `pnpm check:web`    | Solo `web`                                                                |
| `pnpm check:api`    | Solo `api`                                                                |
| `pnpm gen:types`    | Regenera el snapshot OpenAPI y los tipos TS sin arrancar la API           |
| `pnpm format`       | Prettier write                                                            |

### Integración continua

`azure-pipelines.yml` define tres trabajos independientes para Azure Pipelines
sobre un agente Ubuntu hospedado: `web` (instalación congelada, lint, tipos,
pruebas y build de Next), `api` (dependencias con `uv`, Ruff, contrato OpenAPI,
migraciones y pytest contra un PostgreSQL efímero) y `etl` (pruebas unitarias
del ETL). No despliega. Para que corra en una organización nueva hay que tener
concedido el paralelismo de agentes hospedados (Project Settings → Parallel
jobs); sin él, el pipeline queda en cola con el aviso *No hosted parallelism
has been purchased or granted*.

El hook `pre-commit` de Husky formatea con Prettier los archivos del commit.
Si `pnpm` no está en el PATH avisa y deja pasar; para saltarlo a propósito,
`git commit --no-verify`.

### Migraciones (Alembic, desde `apps/api` con venv activado)

```powershell
python -m alembic upgrade head                              # aplicar
python -m alembic revision --autogenerate -m "describe"     # crear nueva
python -m alembic downgrade -1                              # rollback
python -m alembic current                                   # revisión actual
```

## Stack

Frontend: Next.js 15, TypeScript estricto, Tailwind + shadcn/ui, TanStack Query, RHF + Zod.
Backend: FastAPI, SQLAlchemy async, Alembic, Postgres 16, Redis 7, JWT custom (HS256).
Tooling: Turborepo, pnpm workspaces, ESLint, Prettier, Ruff, mypy, Vitest, pytest.

Ver `Docs/styles.md` y `Docs/adr/` para detalles. `Docs/Arquitectura y Stack
Tecnológico - Navifault.txt` es un documento de REFERENCIA EXTERNA que se usó
como base de arquitectura: este producto es Portal Clientes, y `navifault` es
solo uno de sus módulos (`/navifault`, permiso `navifault.view`).

## Observabilidad

- `/health` se conserva por compatibilidad y `/health/live` comprueba el proceso.
- `/health/ready` comprueba Postgres y Redis en paralelo, con timeout; responde `503` sin filtrar detalles internos si una dependencia falla.
- Sentry del backend es opcional: define `SENTRY_DSN` y ajusta `SENTRY_TRACES_SAMPLE_RATE` entre `0` y `1`. Sin DSN no hay tráfico externo ni captura de PII.
- El frontend todavía no envía eventos a Sentry; `NEXT_PUBLIC_SENTRY_DSN` queda reservado para una integración posterior.

## Pruebas de base de datos

Pytest solo acepta una base cuyo nombre sea exactamente `portal_clientes_codex_test`. El arnés aplica `downgrade base` y `upgrade head` para aislar los módulos, por lo que nunca debe apuntarse a una base compartida:

```bash
TEST_DATABASE_URL='postgresql+psycopg://portal_clientes:portal_clientes_dev@localhost:5432/portal_clientes_codex_test' \
DATABASE_URL_LOCAL='' \
uv run --project apps/api pytest -q
```

## ETL de reportes (InformesRendimiento)

El ETL que alimenta Reportes, Navifault y Calidad de datos vive en
`InformesRendimiento/` y forma parte de este repositorio: no hay que clonar nada
aparte. Corre como el servicio `reportes-etl-worker` del perfil `full`, junto a la
API y los demás workers.

Antes de levantar el perfil `full` debe existir su archivo de entorno, porque el
compose lo referencia con `env_file`:

```bash
cp InformesRendimiento/.env.example InformesRendimiento/.env
```

Claves que hay que fijar ahí:

- `MASTER_FERNET_KEY`: **la misma** que usa la API. El ETL descifra con ella las
  credenciales de MyGeotab que la sincronización de la fuente maestra deja en
  `public.geotab_credentials`; con otra clave no puede autenticar.
- `MASTER_DB_URL` y `ANALYTICS_DB_URL`: el compose las sobreescribe con
  `DATABASE_URL`, así que basta con que esa variable esté definida en el `.env`
  de la raíz.

Lo que no hace falta configurar: las credenciales de MyGeotab no se escriben en
el ETL. Se registran en Navi Vehículos, llegan al portal con la sincronización de
la fuente maestra (`daily-sync-worker` a las 04:00 o el botón de Calidad de datos)
y el ETL las lee de la base. Con varias cuentas por base, la extracción reparte
los vehículos entre ellas.

Cómo se ejecuta:

- La corrida diaria la encola `daily-sync-worker`; el ETL la atiende y publica el
  resultado en Calidad de datos (`sync_run`).
- Una corrida manual se dispara desde Calidad de datos con un usuario admin.
- El flujo de fallas de Navifault corre dentro del mismo proceso cada 5 minutos.

Datos locales: el lago Parquet queda en `InformesRendimiento/data_lake/`, la caché
de sesiones en `.session_cache.json` y el estado del watermark heredado en
`estado_ejecucion.json`. Los tres están ignorados por Git y excluidos de la
imagen; la primera corrida los crea.

En producción no hace falta `InformesRendimiento/.env`: el overlay hace que el
worker lea `deploy/production.env`, el mismo archivo de la API, y de ahí toma
`MASTER_FERNET_KEY`. El código va dentro de la imagen, corre sin root y escribe
el lago Parquet y su estado en el volumen `etl_data`.

## Despliegue de producción

El overlay de producción usa imágenes sin bind mounts, procesos no-root, API
interna y una migración one-shot que debe terminar antes de iniciar API y
workers. Cubre todos los servicios del perfil `full`, incluidos el ETL de
reportes y los workers de Navifault.

Archivos de entorno que hay que crear a partir de sus ejemplos (ninguno se
versiona):

| Archivo | Lo lee | Qué contiene |
| --- | --- | --- |
| `deploy/production.env` | API, migración, todos los workers y el ETL | Base de datos, JWT, MinIO, `MASTER_FERNET_KEY`, Navi Vehículos, CloudFleet |
| `deploy/tracking-worker.env` | `seguimiento-etiquetas-worker` | CloudFleet y cadencia del seguimiento de etiquetas |
| `deploy/tracking-ingest.env` | `tracking-ingest-worker` | Base de datos y cadencia de la ingesta |

Antes de arrancar, dos valores que no traen default y sin los cuales una parte
del portal responde 503: `NAVI_API_KEY` (sincronización de la fuente maestra) y
`CLOUDFLEET_API_KEY` + `CLOUDFLEET_ID_REPORTEDBY` (novedades y escalamiento).

Con `ENVIRONMENT=production` o `staging`, la API y todos los workers se niegan
a arrancar si en el entorno queda algún `REPLACE_*`, un secreto de desarrollo,
una `MASTER_FERNET_KEY` vacía o inválida, una contraseña corta, o una URL de
Navi Vehículos o CloudFleet por `http://`. El error nombra la variable, nunca
su valor. Es deliberado: un despliegue con la plantilla sin editar no debe
levantar.

```bash
cp deploy/production.env.example deploy/production.env
cp deploy/tracking-worker.env.example deploy/tracking-worker.env
cp deploy/tracking-ingest.env.example deploy/tracking-ingest.env
# Reemplazar todos los REPLACE_* y ajustar dominios/credenciales.

docker compose --env-file deploy/production.env \
  -f docker-compose.yml -f docker-compose.prod.yml --profile full config
docker compose --env-file deploy/production.env \
  -f docker-compose.yml -f docker-compose.prod.yml --profile full build

# Hacer y verificar el backup de Postgres/MinIO antes del siguiente comando.
docker compose --env-file deploy/production.env \
  -f docker-compose.yml -f docker-compose.prod.yml --profile full up -d
```

Después del arranque, comprobar `/health/live`, `/health/ready`, login, permisos
por flota, Centro de control y Calidad de datos. En Calidad de datos, disparar
una sincronización de la fuente maestra y después una corrida del ETL: la
primera trae vehículos, reglas y credenciales de MyGeotab; la segunda llena el
esquema `analytics` que alimenta Reportes. La primera corrida del ETL extrae
todo el histórico y puede tardar horas.

Hasta que esa primera sincronización de la fuente maestra deje vehículos en la
base, `reportes-etl-worker` arranca, no encuentra credenciales y se reinicia
con `No se encontraron credenciales para 'demo_fleet' en .env`. Es el
comportamiento esperado de un entorno vacío, no un error de configuración: en
cuanto la fuente maestra carga, el siguiente reinicio lo deja en servicio.

Volúmenes con datos: `postgres_data`, `minio_data`, `redis_data`,
`tracking_runtime` y `etl_data`. Un `docker compose down -v` los destruye.

Los riesgos pendientes y la deuda conocida están en `ARQUITECTURA_COMPLETA.md`,
sección 7.

## Contribuir

Ver [CONTRIBUTING.md](CONTRIBUTING.md).
