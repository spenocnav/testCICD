"""Réplica local de la data maestra de Navi Vehículos + config local del ETL.

Navi Vehículos es la fuente de verdad de clientes, bases geotab, credenciales,
reglas y vehículos (ver Docs/contrato-intrgracion-portal-clientes.md). Estas
tablas son la réplica de solo lectura que el job de sync upsertará por
`source_id` (vehículos: por `plate`). Mientras el empalme no exista, se siembran
desde InformesRendimiento (`seed_master_data.py`) con `source_id` NULL.

Regla de oro (§3.1 del contrato): la db física de Geotab se identifica por
`database_key`, no por la fila. Varios clientes pueden compartir la misma db;
reglas y credenciales aplicables a un vehículo son las de TODAS las filas con el
mismo `database_key`.

Extensiones locales (no vienen en el snapshot, editables en el portal):
`vehicles.motor_type/group_key/rpm_class/tank_volume` y las tablas
`motor_catalog`/`motor_rules`/`rpm_rules`, que el ETL de InformesRendimiento
sigue necesitando.

NO confundir con `analytics.dim_vehicle`, que es **output** del ETL.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.fleet import Fleet


class GeotabDatabase(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Base de datos geotab registrada bajo un cliente/flota.

    `database_name` ya NO es único global: la misma db física puede aparecer
    bajo varios clientes (snapshot trae una fila por cliente). `database_key`
    (nombre en minúsculas) identifica la db física compartida.
    """

    __tablename__ = "geotab_databases"
    __table_args__ = (
        UniqueConstraint("fleet_id", "database_name", name="uq_geotab_db_fleet_name"),
        Index("ix_geotab_databases_key", "database_key"),
    )

    source_id: Mapped[int | None] = mapped_column(
        BigInteger, unique=True, nullable=True
    )  # customer_databases.id en Navi Vehículos
    fleet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fleets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    database_name: Mapped[str] = mapped_column(nullable=False)
    database_key: Mapped[str] = mapped_column(nullable=False)
    connection_type: Mapped[str] = mapped_column(nullable=False, default="geotab")
    plate_prefix: Mapped[str | None] = mapped_column(nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    fleet: Mapped[Fleet] = relationship("Fleet", back_populates="geotab_databases")
    credentials: Mapped[list[GeotabCredential]] = relationship(
        "GeotabCredential",
        back_populates="geotab_database",
        cascade="all, delete-orphan",
    )
    vehicles: Mapped[list[Vehicle]] = relationship(
        "Vehicle",
        back_populates="geotab_database",
    )
    rules: Mapped[list[GeotabRule]] = relationship(
        "GeotabRule",
        back_populates="geotab_database",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<GeotabDatabase name={self.database_name!r} key={self.database_key!r}>"


class GeotabCredential(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Credencial geotab de una base; la clave va cifrada Fernet.

    El pool efectivo de un vehículo son las credenciales activas de todas las
    bases con el mismo `database_key`; la rotación usa `last_used_at` (LRU).
    """

    __tablename__ = "geotab_credentials"
    __table_args__ = (
        UniqueConstraint("geotab_database_id", "username", name="uq_credential_db_username"),
    )

    source_id: Mapped[int | None] = mapped_column(
        BigInteger, unique=True, nullable=True
    )  # customer_database_credentials.id en Navi Vehículos
    geotab_database_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("geotab_databases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    username: Mapped[str] = mapped_column(nullable=False)
    password_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    label: Mapped[str | None] = mapped_column(nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    geotab_database: Mapped[GeotabDatabase] = relationship(
        "GeotabDatabase", back_populates="credentials"
    )

    def __repr__(self) -> str:
        return f"<GeotabCredential user={self.username!r}>"


class GeotabRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Regla física de Geotab de una base.

    `rule_id` es el id nativo de Geotab (para `ruleSearch` en ExceptionEvent).
    Reemplaza a la antigua `db_event_rules` (eran solo hábitos seguros).

    Una misma regla física puede aplicarse a varias clasificaciones
    (`operacion` por motor y/o `habito_seguro`) sin duplicar consultas a Geotab.
    Las clasificaciones viven en `GeotabRuleApplication`.
    """

    __tablename__ = "geotab_rules"
    __table_args__ = (
        UniqueConstraint(
            "geotab_database_id",
            "rule_id",
            name="uq_geotab_rule_db_rule",
        ),
    )

    source_id: Mapped[int | None] = mapped_column(
        BigInteger, unique=True, nullable=True
    )  # geotab_rules.id en Navi Vehículos
    geotab_database_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("geotab_databases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rule_id: Mapped[str] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    geotab_database: Mapped[GeotabDatabase] = relationship("GeotabDatabase", back_populates="rules")
    applications: Mapped[list[GeotabRuleApplication]] = relationship(
        "GeotabRuleApplication",
        back_populates="geotab_rule",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<GeotabRule rule_id={self.rule_id!r} name={self.name!r}>"


GEOTAB_RULE_BANDS: tuple[str, ...] = (
    "rango_bajo",
    "rango_economico",
    "rango_balanceado",
    "rango_potencia",
    "rango_potencia_ineficiente",
    "exceso_rpm",
    "ralenti",
)

GEOTAB_SAFE_HABIT_DESCRIPTIONS: tuple[str, ...] = (
    "Excesos de velocidad",
    "Giros bruscos",
    "Excesos de RPM",
    "Frenadas bruscas",
    "Baches o Resaltos fuertes",
    "Aceleraciones bruscas",
)


class GeotabRuleApplication(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Clasificación/aplicación de una regla física Geotab.

    `category` define dónde se reporta. En `operacion`, `motor_type` es
    obligatorio y limita la aplicación a ese motor. En `habito_seguro`, NULL
    aplica a toda la base y un valor limita a ese motor. `event_type` permite
    marcar semántica estable (ej. `exceso_rpm`) sin depender del nombre.

    `band`/`is_descenso` son la declaración explícita de la banda de RPM que mide
    una regla de operación. `description` es la clasificación estable de una
    regla de hábito seguro. Ambos vienen de Navi Vehículos y evitan inferir
    semántica desde `geotab_rules.name`. Los NULL preservan compatibilidad con
    snapshots anteriores.
    """

    __tablename__ = "geotab_rule_applications"
    __table_args__ = (
        CheckConstraint(
            "category IN ('operacion', 'habito_seguro')",
            name="ck_geotab_rule_app_category",
        ),
        CheckConstraint(
            "(category = 'operacion' AND motor_type IS NOT NULL) OR category = 'habito_seguro'",
            name="ck_geotab_rule_app_motor_by_category",
        ),
        CheckConstraint(
            "band IS NULL OR band IN ("
            + ", ".join(f"'{band}'" for band in GEOTAB_RULE_BANDS)
            + ")",
            name="ck_geotab_rule_app_band",
        ),
        CheckConstraint(
            "NOT (band = 'ralenti' AND is_descenso)",
            name="ck_geotab_rule_app_ralenti_no_descenso",
        ),
        CheckConstraint(
            "NOT (is_descenso AND band IS NULL)",
            name="ck_geotab_rule_app_descenso_needs_band",
        ),
        CheckConstraint(
            "description IS NULL OR category = 'habito_seguro'",
            name="ck_geotab_rule_app_description_habito_only",
        ),
        CheckConstraint(
            "description IS NULL OR description IN ("
            + ", ".join(f"'{value}'" for value in GEOTAB_SAFE_HABIT_DESCRIPTIONS)
            + ")",
            name="ck_geotab_rule_app_description",
        ),
        Index(
            "uq_geotab_rule_app_non_null_motor",
            "geotab_rule_id",
            "category",
            "motor_type",
            unique=True,
            postgresql_where=text("motor_type IS NOT NULL"),
        ),
        Index(
            "uq_geotab_rule_app_null_motor",
            "geotab_rule_id",
            "category",
            unique=True,
            postgresql_where=text("motor_type IS NULL"),
        ),
    )

    source_id: Mapped[int | None] = mapped_column(
        BigInteger, unique=True, nullable=True
    )  # geotab_rule_applications.id en Navi Vehículos
    geotab_rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("geotab_rules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category: Mapped[str] = mapped_column(nullable=False, index=True)
    event_type: Mapped[str | None] = mapped_column(nullable=True, index=True)
    motor_type: Mapped[str | None] = mapped_column(
        ForeignKey("motor_catalog.motor_type", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    band: Mapped[str | None] = mapped_column(nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(nullable=True)
    is_descenso: Mapped[bool] = mapped_column(
        default=False, server_default=text("false"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    geotab_rule: Mapped[GeotabRule] = relationship("GeotabRule", back_populates="applications")

    def __repr__(self) -> str:
        return f"<GeotabRuleApplication {self.category}:{self.motor_type}:{self.band}>"


class FleetVehicleGroup(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Grupo interno de vehículos de una flota (categoría/subcategoría).

    Réplica de `customer_vehicle_groups` de Navi Vehículos: el cliente organiza
    su flota como quiera (ej. Bavaria: Regional -> CEDI) y el árbol viaja en el
    snapshot como `customers[].groups` (plano, con `parent_id`). Identidad del
    upsert: `source_id`. Un grupo que desaparece del origen se desactiva, no se
    borra, para no romper vehículos ni filtros guardados.
    """

    __tablename__ = "fleet_vehicle_groups"
    __table_args__ = (
        Index("ix_fleet_vehicle_groups_fleet", "fleet_id"),
        Index("ix_fleet_vehicle_groups_fleet_active", "fleet_id", "is_active"),
    )

    source_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    fleet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fleets.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fleet_vehicle_groups.id", ondelete="SET NULL"),
        nullable=True,
    )  # NULL = categoría raíz
    name: Mapped[str] = mapped_column(nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<FleetVehicleGroup {self.name!r} fleet={self.fleet_id}>"


class Vehicle(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Vehículo. Clave natural: `plate` (única global, upsert del sync).

    Campos del snapshot + extensiones locales del ETL (motor_type, group_key,
    rpm_class, tank_volume) que Navi Vehículos no provee. Solo confiar en
    `geotab_device_id` cuando `geotab_customer_status == 'found'`.
    """

    __tablename__ = "vehicles"
    __table_args__ = (
        Index(
            "ix_vehicles_active_geotab_device_id",
            "geotab_device_id",
            postgresql_where=text(
                "geotab_device_id IS NOT NULL AND is_active IS TRUE"
            ),
        ),
        Index("ix_vehicles_fleet_plate_id", "fleet_id", "plate", "id"),
        Index(
            "ix_vehicles_active_fleet_plate_id",
            "fleet_id",
            "plate",
            "id",
            postgresql_where=text("is_active IS TRUE"),
        ),
    )

    plate: Mapped[str] = mapped_column(unique=True, nullable=False)
    vin: Mapped[str | None] = mapped_column(nullable=True)
    geotab_device_id: Mapped[str | None] = mapped_column(nullable=True)
    geotab_device_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fleet_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fleets.id", ondelete="SET NULL"),
        nullable=True,
    )  # réplica de customer_id en Navi Vehículos
    geotab_database_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("geotab_databases.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    geotab_customer_status: Mapped[str] = mapped_column(
        nullable=False, default="unknown"
    )  # found / not_found / unknown / not_applicable
    engine_number: Mapped[str | None] = mapped_column(nullable=True)
    technical_number: Mapped[str | None] = mapped_column(nullable=True)
    cpl: Mapped[str | None] = mapped_column(nullable=True)
    marca: Mapped[str | None] = mapped_column(nullable=True)
    linea: Mapped[str | None] = mapped_column(nullable=True)
    marketing_model_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    service_model_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    ano_modelo: Mapped[str | None] = mapped_column(nullable=True)
    tipo_combustible: Mapped[str | None] = mapped_column(nullable=True)
    nombre_vehiculo: Mapped[str | None] = mapped_column(nullable=True)
    vocacional: Mapped[bool] = mapped_column(
        default=False, nullable=False
    )  # del snapshot; true = uso vocacional, false = transporte/comercial
    category: Mapped[str] = mapped_column(
        Text, default="Ninguna", nullable=False
    )  # categoría efectiva del cliente: Ninguna | Experiencia Superior | Flota Administrada
    vehicle_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fleet_vehicle_groups.id", ondelete="SET NULL"),
        nullable=True,
    )  # réplica de customer_group_id en Navi Vehículos (grupo interno del cliente)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Extensiones locales del ETL (no vienen en el snapshot).
    motor_type: Mapped[str | None] = mapped_column(
        ForeignKey("motor_catalog.motor_type", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    group_key: Mapped[str | None] = mapped_column(nullable=True)
    rpm_class: Mapped[str | None] = mapped_column(nullable=True)
    tank_volume: Mapped[float | None] = mapped_column(nullable=True)

    fleet: Mapped[Fleet | None] = relationship("Fleet", back_populates="vehicles")
    geotab_database: Mapped[GeotabDatabase | None] = relationship(
        "GeotabDatabase", back_populates="vehicles"
    )

    def __repr__(self) -> str:
        return f"<Vehicle plate={self.plate!r} device={self.geotab_device_id!r}>"


class VehicleExtractionState(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Estado de extracción por `(vehículo, dataset)` para el worker (ETL).

    Reemplaza el watermark global por dataset de InformesRendimiento
    (`estado_ejecucion.json`) por uno por vehículo, para poder backfillear un
    vehículo recién registrado sin re-procesar a toda la flota.

    - `watermark`: próximo `from_date` (último día completo ya extraído). NULL =
      nunca corrió → se usa `backfill_from`.
    - `backfill_from`: fecha mínima a extraer (sembrada al alta o por backfill
      manual). El worker calcula `from_date = COALESCE(watermark, backfill_from)`.
    """

    __tablename__ = "vehicle_extraction_state"
    __table_args__ = (
        UniqueConstraint("vehicle_id", "dataset", name="uq_vehicle_extraction_vehicle_dataset"),
        CheckConstraint(
            "status IN ('pending', 'ok', 'error', 'failed')",
            name="ck_vehicle_extraction_state_status",
        ),
        CheckConstraint(
            "version >= 0",
            name="ck_vehicle_extraction_state_version_nonnegative",
        ),
        Index(
            "ix_vehicle_extraction_pending",
            "dataset",
            "backfill_from",
            "vehicle_id",
            postgresql_where=text("status IN ('pending', 'error', 'failed')"),
        ),
    )

    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vehicles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dataset: Mapped[str] = mapped_column(nullable=False, index=True)
    watermark: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    backfill_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(nullable=False, default="pending")
    last_error: Mapped[str | None] = mapped_column(nullable=True)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    def __repr__(self) -> str:
        return f"<VehicleExtractionState {self.dataset} wm={self.watermark}>"


class SyncState(TimestampMixin, Base):
    """Watermark del job de sync por recurso (ej. 'snapshot').

    `watermark` guarda el `generated_at` de la última respuesta aplicada, no la
    hora local; si el sync falla no avanza y el próximo intento repite el rango.
    """

    __tablename__ = "sync_state"

    key: Mapped[str] = mapped_column(primary_key=True)
    watermark: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Contexto operativo acotado del último ciclo (estado, razones, contadores).
    # Opcional: los recursos que solo necesitan watermark lo dejan en NULL.
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    def __repr__(self) -> str:
        return f"<SyncState {self.key}: {self.watermark}>"


class MotorCatalog(TimestampMixin, Base):
    """Catálogo de tipos de motor (ISD, F2.8, S13, ...). Global, no por cliente.

    La fila la crea el ETL para satisfacer el FK; `description` es config local.
    Las dos velocidades de placa sí vienen del snapshot (contrato §2.6): NULL =
    aún no capturadas en Navi Vehículos, nunca 0.
    """

    __tablename__ = "motor_catalog"
    __table_args__ = (
        CheckConstraint(
            "(governed_speed_rpm IS NULL OR governed_speed_rpm > 0) "
            "AND (max_overspeed_rpm IS NULL OR max_overspeed_rpm > 0) "
            "AND (governed_speed_rpm IS NULL OR max_overspeed_rpm IS NULL "
            "OR max_overspeed_rpm >= governed_speed_rpm)",
            name="ck_motor_catalog_governed_speeds",
        ),
    )

    motor_type: Mapped[str] = mapped_column(primary_key=True)
    description: Mapped[str | None] = mapped_column(nullable=True)
    # Velocidad nominal gobernada sin carga, en RPM.
    governed_speed_rpm: Mapped[int | None] = mapped_column(nullable=True)
    # Capacidad máxima de sobrevelocidad, en RPM.
    max_overspeed_rpm: Mapped[int | None] = mapped_column(nullable=True)

    rules: Mapped[list[MotorRule]] = relationship(
        "MotorRule",
        back_populates="motor",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<MotorCatalog motor_type={self.motor_type!r}>"


class MotorAttachment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Réplica de los adjuntos del motor en Navi Vehículos (curvas de par y
    potencia). Metadatos replicados por el sync; el binario se descarga bajo
    demanda y se cachea en MinIO.

    El binario NO viaja en el snapshot: son ~200 KB por PDF y el snapshot se
    pide completo en cada incremental. La descarga es perezosa (primer clic) y
    queda cacheada, así que un documento que nadie abre nunca se transfiere.

    Detección de cambio: `source_stored_filename` es el nombre del objeto en el
    almacenamiento de Navi, un uuid4 generado en cada carga. Si cambia, el
    binario cambió; `source_updated_at` y `file_size` completan la huella. Al
    detectarlo el sync invalida la caché (`object_key = NULL`,
    `fetch_status = 'pending'`) y el próximo acceso vuelve a descargar.
    """

    __tablename__ = "motor_attachments"
    __table_args__ = (
        UniqueConstraint("source_id", name="uq_motor_attachment_source_id"),
        CheckConstraint(
            "fetch_status IN ('pending', 'ready', 'failed')",
            name="ck_motor_attachment_fetch_status",
        ),
        Index("ix_motor_attachments_motor_type_active", "motor_type", "is_active"),
    )

    # Id del adjunto en Navi Vehículos: la llave del upsert y del endpoint de
    # descarga del proveedor.
    source_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    motor_type: Mapped[str] = mapped_column(
        ForeignKey("motor_catalog.motor_type", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # CPL del adjunto. NULL = documento a nivel de motor, sin CPL declarado.
    cpl: Mapped[str | None] = mapped_column(nullable=True)
    original_filename: Mapped[str | None] = mapped_column(nullable=True)
    content_type: Mapped[str | None] = mapped_column(nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Huella del binario en el origen (ver docstring).
    source_stored_filename: Mapped[str | None] = mapped_column(nullable=True)
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Caché local: NULL mientras el binario no se haya descargado.
    object_key: Mapped[str | None] = mapped_column(nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(nullable=True)
    fetch_status: Mapped[str] = mapped_column(nullable=False, server_default=text("'pending'"))
    fetch_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<MotorAttachment {self.motor_type}:{self.cpl} source_id={self.source_id}>"


class MotorRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Regla de combustible por tipo de motor (= RULES_BY_MOTOR). `rule_id` es el
    id de la regla en geotab; `categoria` clasifica (Rango RPM / RPM Descenso).
    Config local del ETL; no viene en el snapshot."""

    __tablename__ = "motor_rules"
    __table_args__ = (UniqueConstraint("motor_type", "rule_name", name="uq_motor_rule_name"),)

    motor_type: Mapped[str] = mapped_column(
        ForeignKey("motor_catalog.motor_type", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rule_name: Mapped[str] = mapped_column(nullable=False)
    rule_id: Mapped[str] = mapped_column(nullable=False)
    categoria: Mapped[str | None] = mapped_column(nullable=True)

    motor: Mapped[MotorCatalog] = relationship("MotorCatalog", back_populates="rules")

    def __repr__(self) -> str:
        return f"<MotorRule {self.motor_type}:{self.rule_name!r}>"


# Bandas que participan del cálculo por RANGOS DE RPM (flotas con
# `range_mode='rpm'`), en orden ASCENDENTE de revoluciones: definen una
# partición contigua del eje. 'ralenti' no está: en ese modo el ralentí se
# deriva de la telemetría (motor encendido y velocidad 0), no de un tramo.
RPM_RANGE_BANDS: tuple[str, ...] = (
    "rango_bajo",
    "rango_economico",
    "rango_balanceado",
    "rango_potencia",
    "rango_potencia_ineficiente",
    "exceso_rpm",
)


class MotorRpmBand(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Tramo del eje de RPM de un motor (réplica de `motor_rpm_bands` de Navi).

    `rpm_min` inclusivo, `rpm_max` exclusivo; NULL solo en la banda más alta.
    Un motor sin filas está SIN CONFIGURAR: el ETL debe saltarse sus vehículos
    en las flotas `range_mode='rpm'` y el portal lo reporta en calidad de datos.
    """

    __tablename__ = "motor_rpm_bands"
    __table_args__ = (
        UniqueConstraint("motor_type", "band", name="uq_motor_rpm_band"),
        CheckConstraint(
            "band IN (" + ", ".join(f"'{band}'" for band in RPM_RANGE_BANDS) + ")",
            name="ck_motor_rpm_bands_band",
        ),
        CheckConstraint("rpm_min >= 0", name="ck_motor_rpm_bands_min"),
        CheckConstraint(
            "rpm_max IS NULL OR rpm_max > rpm_min", name="ck_motor_rpm_bands_max"
        ),
    )

    motor_type: Mapped[str] = mapped_column(
        ForeignKey("motor_catalog.motor_type", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    band: Mapped[str] = mapped_column(nullable=False)
    rpm_min: Mapped[int] = mapped_column(nullable=False)
    rpm_max: Mapped[int | None] = mapped_column(nullable=True)

    def __repr__(self) -> str:
        return f"<MotorRpmBand {self.motor_type}:{self.band}>"


class RpmRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Regla de exceso RPM por clase RPM (= RPM_RULES). `ordinal` ordena las
    múltiples reglas de una misma clase (con/sin carga).
    Config local del ETL; no viene en el snapshot."""

    __tablename__ = "rpm_rules"
    __table_args__ = (UniqueConstraint("rpm_class", "ordinal", name="uq_rpm_rule_class_ordinal"),)

    rpm_class: Mapped[str] = mapped_column(nullable=False, index=True)
    rule_id: Mapped[str] = mapped_column(nullable=False)
    ordinal: Mapped[int] = mapped_column(default=1, nullable=False)

    def __repr__(self) -> str:
        return f"<RpmRule {self.rpm_class}#{self.ordinal}>"
