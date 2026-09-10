import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Buscar .env en raíz del monorepo y como fallback en apps/api.
_API_DIR = Path(__file__).resolve().parents[2]
_REPO_ROOT = _API_DIR.parent.parent

# Defaults conocidos que deben rechazarse en staging/production.
# Sólo se enumeran aquí con fines de validación: el contenido de los campos
# nunca se incluye en mensajes de error (sólo el nombre de la variable).
_INSECURE_JWT_SECRET = "change-me-in-prod-32-chars-min-secret"
_INSECURE_BOOTSTRAP_PASSWORD = "ChangeMe123!"
_INSECURE_MINIO_ACCESS_KEY = "minioadmin"
_INSECURE_MINIO_SECRET_KEY = "minioadmin"
_INSECURE_DATABASE_PASSWORD = "portal_clientes_dev"

# Prefijos con los que las plantillas y los ejemplos marcan un valor que hay
# que reemplazar. Un despliegue que los conserve arrancaría con secretos
# previsibles (SEC-005), así que en staging/production se rechazan.
_PLACEHOLDER_PREFIXES = ("replace", "change", "todo", "example", "placeholder", "your_", "tu_")
_PLACEHOLDER_FRAGMENTS = ("replace_with", "replace-with", "change-me", "changeme", "<", ">")


def _looks_like_placeholder(value: str | None) -> bool:
    """True si el valor está vacío o es un marcador de plantilla, no un secreto."""
    if value is None:
        return True
    lowered = value.strip().lower()
    if not lowered:
        return True
    if lowered.startswith(_PLACEHOLDER_PREFIXES):
        return True
    return any(fragment in lowered for fragment in _PLACEHOLDER_FRAGMENTS)


def _is_valid_fernet_key(value: str) -> bool:
    try:
        from cryptography.fernet import Fernet

        Fernet(value.encode("utf-8"))
    except Exception:
        return False
    return True


def _url_scheme(value: str) -> str:
    return value.strip().split("://", 1)[0].lower() if "://" in value else ""


class Settings(BaseSettings):
    """Configuración tipada de la aplicación. Lee variables desde .env."""

    model_config = SettingsConfigDict(
        env_file=(_REPO_ROOT / ".env", _API_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        hide_input_in_errors=True,
    )

    # Entorno
    environment: Literal["development", "staging", "production", "test"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    project_name: str = "Portal Clientes API"
    project_version: str = "0.1.0"

    # Observabilidad. Sin DSN no se inicializa ningún transporte externo.
    sentry_dsn: str | None = None
    sentry_traces_sample_rate: float = Field(default=0.0, ge=0.0, le=1.0)

    # Base de datos
    # En Docker: postgresql+psycopg://user:pass@postgres:5432/db
    # En local fuera de Docker, usar DATABASE_URL_LOCAL.
    database_url: str = Field(
        default="postgresql+psycopg://portal_clientes:portal_clientes_dev@localhost:5432/portal_clientes",
    )
    database_url_local: str | None = None
    # Presupuesto por proceso. API y workers importan su propio engine: mantener
    # estos límites pequeños evita multiplicar conexiones hasta agotar Postgres.
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_max_overflow: int = Field(default=5, ge=0, le=50)
    database_pool_timeout_seconds: int = Field(default=10, ge=1, le=120)
    database_pool_recycle_seconds: int = Field(default=1800, ge=60, le=86400)
    # Límites defensivos por conexión del runtime. Las migraciones usan su
    # propio engine y no heredan estos timeouts.
    database_statement_timeout_ms: int = Field(default=30_000, ge=1000, le=600_000)
    database_lock_timeout_ms: int = Field(default=5_000, ge=100, le=60_000)
    database_idle_transaction_timeout_ms: int = Field(default=60_000, ge=1000, le=600_000)
    database_application_name: str = Field(
        default="navi-portal-api",
        min_length=1,
        max_length=63,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    redis_url_local: str | None = None

    # JWT
    jwt_secret: str = Field(default="change-me-in-prod-32-chars-min-secret", min_length=16)
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 60 * 15
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 7

    # Passwords: bcrypt es CPU-bound (~0,5 s por operación con coste 12) y se
    # ejecuta fuera del event loop en un pool dedicado. Este techo evita que una
    # ráfaga de logins consuma toda la CPU del contenedor.
    password_hash_max_concurrency: int = Field(default=4, ge=1, le=32)

    # Auditoría de uso: cada petición autenticada se encola en memoria y un
    # task de fondo la persiste en lote en `usage_events` (nunca en el hot
    # path). Si la cola se llena, los eventos se descartan con un warning:
    # perder telemetría es preferible a frenar peticiones.
    usage_tracking_enabled: bool = True
    usage_flush_interval_seconds: int = Field(default=15, ge=1, le=300)
    usage_flush_batch_size: int = Field(default=500, ge=1, le=5_000)
    usage_queue_max_size: int = Field(default=10_000, ge=100, le=100_000)
    usage_retention_days: int = Field(default=365, ge=7, le=3_650)

    # CORS
    cors_origins: str = "http://localhost:3000"
    # Regex opcional para orígenes (útil en dev: localhost/127.0.0.1/LAN en cualquier puerto).
    cors_origin_regex: str | None = None

    # Analytics (schema gestionado por InformesRendimiento; None = misma DB que portal)
    analytics_database_url: str | None = None

    # Data maestra (vehículos/credenciales/reglas). Key Fernet para cifrar
    # credenciales geotab en reposo; debe compartirse con InformesRendimiento.
    master_fernet_key: str | None = None

    # Sync de data maestra desde Navi Vehículos (contrato de integración).
    # `navi_api_key` viaja en header X-API-Key; debe coincidir con una de las
    # INTEGRATION_API_KEYS de Navi Vehículos. Sin ella el sync no pega (falla).
    navi_base_url: str = "http://localhost:8000"
    navi_api_key: str | None = None
    sync_http_timeout_seconds: int = 120

    # Ingesta de auditoría de syncs externos (InformesRendimiento reporta cada
    # corrida del pipeline de reportes vía POST /data-quality/sync-runs). El
    # caller manda el header X-Sync-Ingest-Key; debe coincidir con este valor.
    # None = endpoint deshabilitado (503).
    sync_ingest_api_key: str | None = None

    # Informe de Ubicaciones: consulta bajo demanda a MyGeotab (posiciones +
    # geocodificación inversa) con la credencial de la base del vehículo, la
    # misma fuente que alimenta el ETL. NADA se persiste: el rastro y las
    # direcciones se piden por día, viajan al cliente y se descartan.
    geotab_api_host: str = "my.geotab.com"
    geotab_timeout_seconds: int = Field(default=60, gt=0, le=300)
    geotab_address_batch_size: int = Field(default=100, ge=1, le=500)
    # Techo de posiciones crudas por día y vehículo antes de muestrear.
    geotab_log_results_limit: int = Field(default=50_000, ge=100, le=200_000)
    # Zona horaria en que el usuario elige las fechas del informe.
    reportes_timezone: str = "America/Bogota"

    # Cloudfleet (Novedades de mantenimiento).
    cloudfleet_api_key: str | None = None
    cloudfleet_id_reportedby: int | None = None
    cloudfleet_base_url: str = "https://fleet.cloudfleet.com/api/v1"
    cloudfleet_timeout_seconds: int = 30
    # Límite publicado por CloudFleet por API key. El cliente también observa
    # X-RateLimit-Remaining/X-RateLimit-Reset para compartir el presupuesto
    # entre GETs paginados y POSTs.
    cloudfleet_requests_per_minute: int = Field(default=30, ge=1, le=30)
    # Lecturas acumuladas MyGeotab -> CloudFleet. Se ejecutan al inicio de la
    # réplica CloudFleet para que vehículos/OTs/cronogramas se lean después de
    # actualizar odómetro y horómetro. `source_code` se omite por defecto:
    # CloudFleet exige que, si se configura, ya exista en su catálogo.
    cloudfleet_meter_sync_enabled: bool = True
    cloudfleet_meter_lookback_days: int = Field(default=7, ge=1, le=31)
    cloudfleet_meter_source_code: str | None = Field(default=None, max_length=64)
    cloudfleet_meter_comment: str | None = Field(
        default="Lectura automática desde MyGeotab",
        max_length=100,
    )
    # Worker de réplica CloudFleet (OTs/cronogramas/vehículos). Loop por
    # intervalo, mismo patrón que refresh_token_purge; default diario. Vive
    # tras `profiles:["full"]` en docker-compose (apagado por defecto).
    cloudfleet_sync_interval_seconds: int = Field(default=60 * 60 * 24, gt=0)

    # Orquestador diario de sincronizaciones (servicio `daily-sync-worker`).
    # A la hora local configurada corre, EN ORDEN y de a una: fuente maestra
    # (Navi Vehículos) → ETL de reportes (encola en `etl_trigger_request` y
    # espera a que el worker del ETL la cierre) → réplica CloudFleet. Un paso
    # que falla no detiene a los siguientes: son fuentes independientes y un
    # proveedor caído no debe dejar al resto sin datos frescos.
    daily_sync_enabled: bool = True
    # Hora local "HH:MM" en `daily_sync_timezone`, no en el reloj del host
    # (contenedores en UTC): "04:00" America/Bogota = 09:00 UTC.
    daily_sync_at: str = Field(default="04:00", pattern=r"^([01]\d|2[0-3]):([0-5]\d)$")
    daily_sync_timezone: str = "America/Bogota"
    # Techo de espera del paso de reportes. El pipeline completo (extract →
    # transform → load) puede durar horas; pasado el techo se deja la solicitud
    # en curso y se sigue con CloudFleet en vez de perder la ventana diaria.
    daily_sync_etl_timeout_seconds: int = Field(default=6 * 60 * 60, gt=0)
    daily_sync_etl_poll_seconds: float = Field(default=30.0, gt=0)
    # Días hacia atrás desde el watermark para re-traer OTs de CloudFleet.
    daily_sync_cloudfleet_refresh_days: int = Field(default=45, gt=0)
    # Paso 4 de la cadena diaria: replica `isDone`/`doneAt`/`workOrderDoneNumber`
    # de las issues de CloudFleet sobre `novedades.external_*`. Sólo mira las
    # novedades enviadas que no constan como resueltas.
    daily_sync_novedades_enabled: bool = True
    # Con pocas candidatas se consulta cada issue por número (una petición por
    # novedad, exacta). Por encima de este umbral se recorre el listado completo
    # paginado (50 por página), porque CloudFleet IGNORA `createdAtFrom` y
    # `reportedAtFrom`, y `reportedBy` devuelve un subconjunto incompleto
    # (verificado 2026-08-28): no hay forma de pedir "sólo las del portal".
    novedad_status_sync_list_threshold: int = Field(default=25, ge=0)
    # En modo listado, las candidatas que no aparezcan se consultan una a una
    # hasta este techo por corrida, para que un desfase del proveedor no se
    # coma el presupuesto de 30 req/min que comparte con los demás syncs.
    novedad_status_sync_max_single_lookups: int = Field(default=20, ge=0)

    # Vigilante de escalamientos de Navifault. Sondea sólo las novedades
    # escaladas con el ciclo de su falla abierto: CloudFleet no ofrece webhook
    # de novedades ni de trabajos, y la transición que más importa —borrar el
    # trabajo de la orden, que reabre la novedad— no emite ningún evento.
    navifault_escalation_watch_enabled: bool = True
    #: Con 10 escalamientos abiertos son ~2 peticiones cada uno por ciclo. A 900
    #: segundos eso es 1,3 por minuto sobre un presupuesto de 30 compartido con
    #: la cadena diaria; bajarlo aprieta ese presupuesto.
    navifault_escalation_watch_interval_seconds: int = Field(default=900, ge=60)

    # Storage S3-compatible. Novedades y Navifault usan buckets privados
    # distintos para que sus políticas, ciclo de vida y auditoría no se mezclen.
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "novedades"
    navifault_minio_bucket: str = "navifault-manuals"
    #: Navegación por los enlaces internos del documento del corpus. APAGADA:
    #: la resolución contra el corpus funciona —42 de 42 enlaces de la muestra—
    #: pero los análisis y los documentos técnicos todavía no se renderizan
    #: bien, y llevar a alguien a un documento mal pintado es peor que no
    #: llevarlo. Con esto en `false` los `href` se neutralizan como siempre y
    #: el visor sirve sólo la FC.
    navifault_corpus_navigation_enabled: bool = False
    # Caché local de los adjuntos de motor de Navi Vehículos (curvas de par y
    # potencia). Bucket propio: son documentos del maestro, no evidencias de
    # una novedad ni manuales de Navifault.
    motor_curves_minio_bucket: str = "motor-curves"
    # Enfriamiento antes de reintentar una descarga que falló. Sin él, una
    # pantalla abierta reintentaría contra Navi en cada render.
    motor_curve_retry_cooldown_seconds: int = 300
    motor_curve_http_timeout_seconds: int = 30
    # LLM bajo demanda para la descripción técnica. Permanece deshabilitado
    # hasta que el servidor configure explícitamente su endpoint privado.
    navifault_llm_enabled: bool = False
    navifault_llm_base_url: str | None = None
    navifault_llm_model: str = "local-qwen"
    navifault_llm_timeout_seconds: float = Field(default=900.0, gt=0, le=3600)
    navifault_llm_temperature: float = Field(default=0.3, ge=0, le=2)
    # Techo de salida por generación. `None` omite `max_tokens` de la petición:
    # el proveedor (llama-swap, sin `--n-predict` configurado) no impone tope
    # propio, así que el límite efectivo pasa a ser
    # `navifault_llm_timeout_seconds`: se acota por tiempo, no por tokens.
    #
    # El techo era la causa del modo de fallo más caro del módulo: un modelo con
    # razonamiento gasta el presupuesto pensando y devuelve `content` vacío si
    # no alcanza a escribir el JSON. Con 900 tokens, 16 de 17 comunicaciones
    # quedaban en `failed` (2026-08-26). Con 4.000 tampoco basta: medido el
    # 2026-08-27, generaciones reales con razonamiento gastaron 5.328 y 5.295.
    # No hay presión de costo que justifique arriesgar esa falla.
    navifault_llm_max_output_tokens: Annotated[int, Field(ge=512, le=32000)] | None = None
    # Razonamiento apagado. Medido el 2026-08-27 sobre 12 fallas, con el mismo
    # prompt y la misma temperatura: en juicio ciego humano empató 6-6 contra la
    # variante directa —no gana preferencia—, cuesta 7x en tiempo (156 s de
    # mediana contra ~22 s) y empeora de forma medible la redacción al cliente.
    # Al razonar, el modelo recita el contexto y ese andamiaje se le filtra a la
    # respuesta: abre nombrando el código de falla en 7 de 12 casos (la variante
    # directa, 0 de 12), cita la referencia del motor en 2 de 12 (directa, 0) y
    # abre con "Navitrans" en 2 de 12 (directa, 0).
    navifault_llm_disable_thinking: bool = True
    # El worker es serial por defecto: el modelo local comparte GPU con otros
    # servicios y una sola generación en paralelo evita agotar su memoria.
    navifault_llm_worker_batch_size: int = Field(default=1, ge=1, le=8)
    # Dos carriles simultáneos sobre el proveedor: uno consume la cola
    # pregenerada y otro queda reservado para la ficha que un usuario acaba de
    # abrir, de modo que no espere a que termine el lote de fondo. Medido el
    # 2026-08-27, el servidor acepta 4 solicitudes a la vez pero su throughput
    # no crece: la GPU se reparte. Por eso son 2 y no más — la GPU además se
    # comparte con otros servicios.
    navifault_llm_interactive_slots: int = Field(default=1, ge=1, le=4)
    navifault_llm_background_slots: int = Field(default=1, ge=1, le=4)
    navifault_llm_worker_poll_seconds: float = Field(default=2.0, gt=0, le=60)
    navifault_llm_processing_stale_seconds: int = Field(default=3600, ge=900, le=86400)
    # Barrido que pregenera las fallas recientes para que la ficha ya tenga su
    # comunicación al abrirse. Sólo encola llaves con FC Cummins exacta: una
    # llave ambigua o sin página no se resuelve por intuición. Medido el
    # 2026-08-26, un día completo son 55 páginas distintas y resolver las 395
    # llaves de esa ventana cuesta ~5 s de SQL.
    navifault_llm_sweep_enabled: bool = True
    navifault_llm_sweep_interval_seconds: float = Field(default=300.0, ge=60, le=86400)
    navifault_llm_sweep_hours: int = Field(default=24, ge=1, le=720)
    navifault_llm_sweep_max_enqueued: int = Field(default=200, ge=1, le=5000)
    minio_secure: bool = False

    # Outbox transaccional de Novedades (worker que envía a Cloudfleet).
    # `stale_lock_seconds` es el umbral tras el cual un item `processing` sin
    # respuesta se considera abandonado y otro worker puede re-clamarlo.
    novedad_outbox_batch_size: int = 10
    novedad_outbox_poll_interval_seconds: float = 5.0
    novedad_outbox_stale_lock_seconds: int = 120
    novedad_outbox_max_attempts: int = 10
    # Backoff exponencial (segundos) tras un fallo. El item queda con
    # available_at = now() + base * 2^(attempts-1), capeado por max.
    novedad_outbox_backoff_base_seconds: float = 30.0
    novedad_outbox_backoff_max_seconds: float = 3600.0

    # Housekeeping de refresh tokens (proceso independiente del API).
    # La purga elimina filas que están revocadas desde hace más de
    # `retention_seconds` o que expiraron hace más de ese margen.
    # El margen evita perder evidencia forense de una sesión recién
    # rotada/revocada. Batch limita cuántas filas se borran por pasada
    # para no sostener un lock largo.
    refresh_token_purge_interval_seconds: int = Field(default=3600, gt=0)
    refresh_token_purge_retention_seconds: int = Field(default=60 * 60 * 24 * 7, ge=0)
    refresh_token_purge_batch_size: int = Field(default=1000, gt=0)

    # Extracción (worker ETL): margen de días ANTES del día de registro del
    # vehículo (`vehicles.created_at`) para el backfill por defecto. 0 = arranca
    # exactamente el día que se registra; para extraer historia previa se usa
    # "Reprocesar".
    extraction_default_lookback_days: int = 0

    # Calidad de datos: una réplica sin actualización durante esta ventana se
    # marca como vencida en el centro de control.
    data_quality_stale_hours: int = Field(default=48, gt=0, le=24 * 30)

    # Bootstrap admin (Fase 2)
    bootstrap_admin_email: str = "admin@portalclientes.local"
    bootstrap_admin_password: str = "ChangeMe123!"
    bootstrap_admin_name: str = "Administrador"

    @field_validator("cors_origins")
    @classmethod
    def _strip_cors(cls, v: str) -> str:
        return v.strip()

    @field_validator("daily_sync_timezone")
    @classmethod
    def _known_timezone(cls, v: str) -> str:
        """Falla al arrancar, no a las 04:00: una tz inválida haría que el
        orquestador nunca dispare."""
        name = v.strip()
        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"zona horaria desconocida: {name!r}") from exc
        return name

    @model_validator(mode="after")
    def _enforce_environment_safety(self) -> "Settings":
        env = self.environment
        errors: list[str] = []

        def _add(name: str, msg: str) -> None:
            # Nunca incluir el valor; sólo el nombre de la variable.
            errors.append(f"{name}: {msg}")

        # Secretos de ejemplo, placeholders y valores débiles: staging y production.
        if env in {"staging", "production"}:
            if self.jwt_secret == _INSECURE_JWT_SECRET or _looks_like_placeholder(self.jwt_secret):
                _add("jwt_secret", "secret de desarrollo o placeholder en entorno seguro")
            elif len(self.jwt_secret) < 32:
                _add("jwt_secret", "debe tener al menos 32 caracteres en entorno seguro")
            elif len(set(self.jwt_secret)) < 8:
                _add("jwt_secret", "demasiado repetitivo; usar un valor aleatorio")

            if self.bootstrap_admin_password == _INSECURE_BOOTSTRAP_PASSWORD or (
                _looks_like_placeholder(self.bootstrap_admin_password)
            ):
                _add(
                    "bootstrap_admin_password",
                    "contraseña de bootstrap por defecto o placeholder en entorno seguro",
                )
            elif len(self.bootstrap_admin_password) < 12:
                _add("bootstrap_admin_password", "debe tener al menos 12 caracteres")
            if "@example." in self.bootstrap_admin_email.lower():
                _add("bootstrap_admin_email", "correo de ejemplo en entorno seguro")

            if self.minio_access_key == _INSECURE_MINIO_ACCESS_KEY or _looks_like_placeholder(
                self.minio_access_key
            ):
                _add(
                    "minio_access_key",
                    "clave de acceso MinIO por defecto o placeholder en entorno seguro",
                )
            if self.minio_secret_key == _INSECURE_MINIO_SECRET_KEY or _looks_like_placeholder(
                self.minio_secret_key
            ):
                _add(
                    "minio_secret_key",
                    "clave secreta MinIO por defecto o placeholder en entorno seguro",
                )

            # Contraseña de la base: la de desarrollo o un placeholder de plantilla.
            db_password = self._database_password()
            if db_password is not None and (
                db_password == _INSECURE_DATABASE_PASSWORD or _looks_like_placeholder(db_password)
            ):
                _add(
                    "database_url",
                    "contraseña de desarrollo o placeholder en entorno seguro",
                )

            # La key Fernet cifra las credenciales de MyGeotab que llegan de la
            # fuente maestra; sin ella el sync y el ETL fallan en tiempo de
            # ejecución, lejos de la configuración que lo causó.
            if _looks_like_placeholder(self.master_fernet_key):
                _add("master_fernet_key", "requerida en entorno seguro (vacía o placeholder)")
            elif not _is_valid_fernet_key(self.master_fernet_key or ""):
                _add("master_fernet_key", "no es una key Fernet válida")

            # Tokens de integración: opcionales, pero nunca un placeholder.
            for name, value in (
                ("navi_api_key", self.navi_api_key),
                ("cloudfleet_api_key", self.cloudfleet_api_key),
                ("sync_ingest_api_key", self.sync_ingest_api_key),
            ):
                if value is not None and value.strip() and _looks_like_placeholder(value):
                    _add(name, "placeholder de plantilla en entorno seguro")

            # SEC-032: por estas URLs viajan la X-API-Key de Navi Vehículos y el
            # Bearer de CloudFleet. En entorno seguro sólo HTTPS.
            for name, value in (
                ("navi_base_url", self.navi_base_url),
                ("cloudfleet_base_url", self.cloudfleet_base_url),
            ):
                if _url_scheme(value) != "https":
                    _add(name, "debe usar https en entorno seguro")

        # CORS inseguro: solo production
        if env == "production":
            origins = self.cors_origins_list
            if not origins:
                _add("cors_origins", "lista vacía en production")
            else:
                for origin in origins:
                    if origin == "*":
                        _add("cors_origins", "origen '*' no permitido en production")
                    lowered = origin.lower().strip()
                    host = lowered.split("//", 1)[-1].split(":", 1)[0].split("/", 1)[0]
                    if host in {"localhost", "127.0.0.1"}:
                        _add(
                            "cors_origins",
                            "origen basado en localhost/127.0.0.1 no permitido en production",
                        )

            regex_raw = (self.cors_origin_regex or "").strip()
            if regex_raw:
                # Anclas y espacios no cambian que el patrón acepte todo.
                # Los escapes sí cambian el significado y no se eliminan.
                unanchored = re.sub(r"^\^+|\$+$", "", regex_raw).strip()
                if unanchored == ".*":
                    _add(
                        "cors_origin_regex",
                        "regex universal '.*' no permitido en production",
                    )

        if errors:
            joined = "; ".join(errors)
            raise ValueError(f"Configuración insegura para environment={env!r}: {joined}")

        return self

    def _database_password(self) -> str | None:
        """Contraseña embebida en DATABASE_URL, o None si no se puede leer."""
        try:
            from sqlalchemy.engine import make_url

            return make_url(self.database_url).password
        except Exception:
            return None

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def effective_cors_origin_regex(self) -> str | None:
        """En dev, si no se define regex, acepta localhost/127.0.0.1 en cualquier puerto."""
        if self.cors_origin_regex:
            return self.cors_origin_regex
        if self.environment in {"development", "test"}:
            # Dev: aceptar cualquier origen (localhost, LAN, Tailscale 100.x, etc.).
            # Producción NO entra aquí: usa solo CORS_ORIGINS explícito.
            return r".*"
        return None

    @property
    def effective_database_url(self) -> str:
        """Si existe DATABASE_URL_LOCAL y entorno no es production, úsalo (para correr fuera de Docker)."""
        if self.database_url_local and self.environment in {"development", "test"}:
            return self.database_url_local
        return self.database_url

    @property
    def effective_redis_url(self) -> str:
        if self.redis_url_local and self.environment in {"development", "test"}:
            return self.redis_url_local
        return self.redis_url


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
