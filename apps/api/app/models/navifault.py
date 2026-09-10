"""Corpus técnico Cummins de Navifault.

Las entidades viven en el schema privado ``navifault``. El HTML oficial y los
binarios se conservan en MinIO; Postgres guarda sus hashes, claves de objeto y
la estructura necesaria para resolver una falla y navegar su grafo técnico.

No son tablas ``analytics``: pertenecen al dominio operacional del Portal y se
versionan mediante Alembic.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

NAVIFAULT_SCHEMA = "navifault"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class NavifaultCorpusImportRun(Base):
    __tablename__ = "corpus_import_runs"
    __table_args__ = {"schema": NAVIFAULT_SCHEMA}  # noqa: RUF012

    import_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    corpus_version: Mapped[str] = mapped_column(String(96), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    source_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    audit: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NavifaultManual(Base):
    __tablename__ = "manuals"
    __table_args__ = (
        CheckConstraint("language IN ('ES', 'EN')", name="ck_navifault_manual_language"),
        CheckConstraint(
            "source_status IN ('ready', 'incomplete')", name="ck_navifault_manual_status"
        ),
        {"schema": NAVIFAULT_SCHEMA},
    )

    pub_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    language: Mapped[str] = mapped_column(String(2), nullable=False)
    engine_models: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    fault_pages_total: Mapped[int] = mapped_column(Integer, nullable=False)
    source_status: Mapped[str] = mapped_column(String(24), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


class NavifaultEngineManualCandidate(Base):
    """Evidencia ESN → publicación importada desde el inventario Cummins.

    No es la llave de resolución de Navifault en runtime. Sirve para verificar
    y ampliar el mapa dateplate → manual cuando llegan vehículos nuevos.
    """

    __tablename__ = "engine_manual_candidates"
    __table_args__ = (
        CheckConstraint(
            "candidate_type IN ('primary', 'alternate')",
            name="ck_navifault_engine_candidate_type",
        ),
        Index("ix_navifault_engine_candidate_pub", "pub_id"),
        {"schema": NAVIFAULT_SCHEMA},
    )

    engine_serial: Mapped[str] = mapped_column(String(64), primary_key=True)
    pub_id: Mapped[str] = mapped_column(
        ForeignKey(f"{NAVIFAULT_SCHEMA}.manuals.pub_id", ondelete="CASCADE"), primary_key=True
    )
    cpl: Mapped[str | None] = mapped_column(String(64), nullable=True)
    candidate_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_record: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class NavifaultFaultPage(Base):
    __tablename__ = "fault_pages"
    __table_args__ = (
        CheckConstraint("language IN ('ES', 'EN')", name="ck_navifault_fault_page_language"),
        CheckConstraint(
            "raw_html_status IN ('available', 'missing')", name="ck_navifault_page_html"
        ),
        UniqueConstraint(
            "pub_id", "fault_code", "variant", "source_url", name="uq_navifault_page_source"
        ),
        Index("ix_navifault_fault_pages_pub_code", "pub_id", "fault_code"),
        {"schema": NAVIFAULT_SCHEMA},
    )

    fault_page_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    pub_id: Mapped[str] = mapped_column(
        ForeignKey(f"{NAVIFAULT_SCHEMA}.manuals.pub_id", ondelete="CASCADE"), nullable=False
    )
    language: Mapped[str] = mapped_column(String(2), nullable=False)
    engine_model: Mapped[str | None] = mapped_column(String(512), nullable=True)
    fault_code: Mapped[int] = mapped_column(Integer, nullable=False)
    variant: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    raw_html_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_html_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_html_status: Mapped[str] = mapped_column(String(16), nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    sections: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    source_record: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class NavifaultFaultProtocolKey(Base):
    __tablename__ = "fault_protocol_keys"
    __table_args__ = (
        CheckConstraint(
            "protocol IN ('J1939', 'J1708')", name="ck_navifault_protocol_key_protocol"
        ),
        CheckConstraint(
            "namespace IN ('SPN', 'PID', 'SID')", name="ck_navifault_protocol_key_namespace"
        ),
        CheckConstraint(
            "match_specificity IN ('code_fmi', 'code_only')",
            name="ck_navifault_protocol_key_specificity",
        ),
        Index(
            "ix_navifault_protocol_lookup",
            "protocol",
            "namespace",
            "diagnostic_code",
            "fmi",
        ),
        {"schema": NAVIFAULT_SCHEMA},
    )

    fault_protocol_key_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    fault_page_id: Mapped[str] = mapped_column(
        ForeignKey(f"{NAVIFAULT_SCHEMA}.fault_pages.fault_page_id", ondelete="CASCADE"),
        nullable=False,
    )
    protocol: Mapped[str] = mapped_column(String(8), nullable=False)
    namespace: Mapped[str] = mapped_column(String(8), nullable=False)
    diagnostic_code: Mapped[int] = mapped_column(Integer, nullable=False)
    fmi: Mapped[int | None] = mapped_column(Integer, nullable=True)
    match_specificity: Mapped[str] = mapped_column(String(16), nullable=False)
    source_codes: Mapped[str] = mapped_column(Text, nullable=False)


class NavifaultFaultPageTable(Base):
    __tablename__ = "fault_page_tables"
    __table_args__ = {"schema": NAVIFAULT_SCHEMA}  # noqa: RUF012

    fault_page_id: Mapped[str] = mapped_column(
        ForeignKey(f"{NAVIFAULT_SCHEMA}.fault_pages.fault_page_id", ondelete="CASCADE"),
        primary_key=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_table_html: Mapped[str] = mapped_column(Text, nullable=False)
    table_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class NavifaultFaultAnalysis(Base):
    __tablename__ = "fault_analyses"
    __table_args__ = (
        CheckConstraint(
            "raw_html_status IN ('available', 'missing')", name="ck_navifault_analysis_html"
        ),
        {"schema": NAVIFAULT_SCHEMA},
    )

    analysis_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    source_url: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_html_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_html_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_html_status: Mapped[str] = mapped_column(String(16), nullable=False)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    source_analysis: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )


class NavifaultFaultPageAnalysis(Base):
    __tablename__ = "fault_page_analyses"
    __table_args__ = {"schema": NAVIFAULT_SCHEMA}  # noqa: RUF012

    fault_page_id: Mapped[str] = mapped_column(
        ForeignKey(f"{NAVIFAULT_SCHEMA}.fault_pages.fault_page_id", ondelete="CASCADE"),
        primary_key=True,
    )
    analysis_id: Mapped[str] = mapped_column(
        ForeignKey(f"{NAVIFAULT_SCHEMA}.fault_analyses.analysis_id", ondelete="CASCADE"),
        primary_key=True,
    )


class NavifaultFaultAnalysisTable(Base):
    __tablename__ = "fault_analysis_tables"
    __table_args__ = {"schema": NAVIFAULT_SCHEMA}  # noqa: RUF012

    analysis_id: Mapped[str] = mapped_column(
        ForeignKey(f"{NAVIFAULT_SCHEMA}.fault_analyses.analysis_id", ondelete="CASCADE"),
        primary_key=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_table_html: Mapped[str] = mapped_column(Text, nullable=False)
    table_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class NavifaultTechnicalDocument(Base):
    __tablename__ = "technical_documents"
    __table_args__ = (
        CheckConstraint(
            "raw_html_status IN ('available', 'missing')", name="ck_navifault_document_html"
        ),
        {"schema": NAVIFAULT_SCHEMA},
    )

    document_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    source_url: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_html_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_html_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_html_status: Mapped[str] = mapped_column(String(16), nullable=False)
    text_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    sections: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    cautions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    source_document: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class NavifaultTechnicalDocumentTable(Base):
    __tablename__ = "technical_document_tables"
    __table_args__ = {"schema": NAVIFAULT_SCHEMA}  # noqa: RUF012

    document_id: Mapped[str] = mapped_column(
        ForeignKey(f"{NAVIFAULT_SCHEMA}.technical_documents.document_id", ondelete="CASCADE"),
        primary_key=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_table_html: Mapped[str] = mapped_column(Text, nullable=False)
    table_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class NavifaultFaultAnalysisDocument(Base):
    __tablename__ = "fault_analysis_documents"
    __table_args__ = {"schema": NAVIFAULT_SCHEMA}  # noqa: RUF012

    analysis_id: Mapped[str] = mapped_column(
        ForeignKey(f"{NAVIFAULT_SCHEMA}.fault_analyses.analysis_id", ondelete="CASCADE"),
        primary_key=True,
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey(f"{NAVIFAULT_SCHEMA}.technical_documents.document_id", ondelete="CASCADE"),
        primary_key=True,
    )
    link_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    link_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, primary_key=True)


class NavifaultFaultPageDocument(Base):
    __tablename__ = "fault_page_documents"
    __table_args__ = (
        CheckConstraint(
            "link_origin IN ('fc_record', 'analysis_discovery')",
            name="ck_navifault_page_document_origin",
        ),
        {"schema": NAVIFAULT_SCHEMA},
    )

    fault_page_id: Mapped[str] = mapped_column(
        ForeignKey(f"{NAVIFAULT_SCHEMA}.fault_pages.fault_page_id", ondelete="CASCADE"),
        primary_key=True,
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey(f"{NAVIFAULT_SCHEMA}.technical_documents.document_id", ondelete="CASCADE"),
        primary_key=True,
    )
    link_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    link_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, primary_key=True)
    link_origin: Mapped[str] = mapped_column(String(24), nullable=False)


class NavifaultTechnicalDocumentLink(Base):
    __tablename__ = "technical_document_links"
    __table_args__ = {"schema": NAVIFAULT_SCHEMA}  # noqa: RUF012

    parent_document_id: Mapped[str] = mapped_column(
        ForeignKey(f"{NAVIFAULT_SCHEMA}.technical_documents.document_id", ondelete="CASCADE"),
        primary_key=True,
    )
    child_document_id: Mapped[str] = mapped_column(
        ForeignKey(f"{NAVIFAULT_SCHEMA}.technical_documents.document_id", ondelete="CASCADE"),
        primary_key=True,
    )
    link_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    link_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, primary_key=True)


class NavifaultAsset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint(
            "local_status IN ('available', 'missing')", name="ck_navifault_asset_status"
        ),
        {"schema": NAVIFAULT_SCHEMA},
    )

    asset_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    source_url: Mapped[str | None] = mapped_column(Text, unique=True, nullable=True)
    object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    local_status: Mapped[str] = mapped_column(String(16), nullable=False)


class NavifaultFaultPageAsset(Base):
    __tablename__ = "fault_page_assets"
    __table_args__ = {"schema": NAVIFAULT_SCHEMA}  # noqa: RUF012

    fault_page_id: Mapped[str] = mapped_column(
        ForeignKey(f"{NAVIFAULT_SCHEMA}.fault_pages.fault_page_id", ondelete="CASCADE"),
        primary_key=True,
    )
    asset_id: Mapped[str] = mapped_column(
        ForeignKey(f"{NAVIFAULT_SCHEMA}.assets.asset_id", ondelete="CASCADE"), primary_key=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    high_res_url: Mapped[str | None] = mapped_column(Text, nullable=True)


class NavifaultFaultAnalysisAsset(Base):
    __tablename__ = "fault_analysis_assets"
    __table_args__ = {"schema": NAVIFAULT_SCHEMA}  # noqa: RUF012

    analysis_id: Mapped[str] = mapped_column(
        ForeignKey(f"{NAVIFAULT_SCHEMA}.fault_analyses.analysis_id", ondelete="CASCADE"),
        primary_key=True,
    )
    asset_id: Mapped[str] = mapped_column(
        ForeignKey(f"{NAVIFAULT_SCHEMA}.assets.asset_id", ondelete="CASCADE"), primary_key=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    high_res_url: Mapped[str | None] = mapped_column(Text, nullable=True)


class NavifaultTechnicalDocumentAsset(Base):
    __tablename__ = "technical_document_assets"
    __table_args__ = {"schema": NAVIFAULT_SCHEMA}  # noqa: RUF012

    document_id: Mapped[str] = mapped_column(
        ForeignKey(f"{NAVIFAULT_SCHEMA}.technical_documents.document_id", ondelete="CASCADE"),
        primary_key=True,
    )
    asset_id: Mapped[str] = mapped_column(
        ForeignKey(f"{NAVIFAULT_SCHEMA}.assets.asset_id", ondelete="CASCADE"), primary_key=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    high_res_url: Mapped[str | None] = mapped_column(Text, nullable=True)


class NavifaultDateplateManualMap(Base):
    __tablename__ = "dateplate_manual_map"
    __table_args__ = (
        CheckConstraint(
            "mapping_status IN ('verified', 'pending', 'retired')",
            name="ck_navifault_dateplate_status",
        ),
        Index("ix_navifault_dateplate_lookup", "normalized_service_model_name", "priority"),
        {"schema": NAVIFAULT_SCHEMA},
    )

    normalized_service_model_name: Mapped[str] = mapped_column(String(512), primary_key=True)
    pub_id: Mapped[str] = mapped_column(
        ForeignKey(f"{NAVIFAULT_SCHEMA}.manuals.pub_id", ondelete="RESTRICT"), primary_key=True
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    mapping_status: Mapped[str] = mapped_column(String(24), nullable=False)
    mapping_evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class NavifaultFaultPageLanguageLink(Base):
    __tablename__ = "fault_page_language_links"
    __table_args__ = (
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_navifault_language_confidence"
        ),
        CheckConstraint(
            "status IN ('candidate', 'verified', 'rejected')", name="ck_navifault_language_status"
        ),
        {"schema": NAVIFAULT_SCHEMA},
    )

    source_fault_page_id: Mapped[str] = mapped_column(
        ForeignKey(f"{NAVIFAULT_SCHEMA}.fault_pages.fault_page_id", ondelete="CASCADE"),
        primary_key=True,
    )
    target_fault_page_id: Mapped[str] = mapped_column(
        ForeignKey(f"{NAVIFAULT_SCHEMA}.fault_pages.fault_page_id", ondelete="CASCADE"),
        primary_key=True,
    )
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)


class NavifaultGeneratedClientDescription(Base):
    """Dos comunicaciones de cliente derivadas de una FC Cummins exacta."""

    __tablename__ = "generated_client_descriptions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed')",
            name="ck_navifault_client_description_status",
        ),
        UniqueConstraint(
            "fault_page_id",
            "context_sha256",
            "prompt_version",
            "model_version",
            name="uq_navifault_client_description_cache",
        ),
        Index("ix_navifault_client_description_status", "status", "updated_at"),
        Index(
            "ix_navifault_client_description_queue",
            "status",
            text("priority DESC"),
            "created_at",
        ),
        {"schema": NAVIFAULT_SCHEMA},
    )

    description_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    fault_page_id: Mapped[str] = mapped_column(
        ForeignKey(f"{NAVIFAULT_SCHEMA}.fault_pages.fault_page_id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Mayor primero. Lo pide un usuario con la ficha abierta (interactivo) se
    #: atiende antes que lo que dejó el barrido de pregeneración.
    priority: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0"), default=0
    )
    context_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(96), nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    descripcion_correo_cliente: Mapped[str | None] = mapped_column(Text, nullable=True)
    descripcion_plataforma_cliente: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class NavifaultManagedFaultCase(Base):
    """Estado operacional de una firma de falla para un vehículo del Portal.

    La fuente de eventos continúa siendo ``analytics.fact_fault_event``. Esta
    entidad únicamente conserva la última gestión humana y su punto de corte;
    con ello una aparición posterior puede detectarse como repetición sin
    modificar ni duplicar los hechos provenientes de Geotab.
    """

    __tablename__ = "managed_fault_cases"
    __table_args__ = (
        UniqueConstraint("vehicle_id", "signature_sha256", name="uq_navifault_managed_case"),
        Index("ix_navifault_managed_case_vehicle", "vehicle_id", "last_managed_at"),
        Index(
            "ix_navifault_managed_case_escalated",
            "escalated_novedad_id",
            postgresql_where=text("escalated_novedad_id IS NOT NULL"),
        ),
        {"schema": NAVIFAULT_SCHEMA},
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False
    )
    signature_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    diagnostic_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failure_mode: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_amber: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    stop_red: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    malfunction: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    warning: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    managed_through_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    managed_through_row_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    current_cycle_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Nullable desde `e8f9a0b10057`: un caso creado SÓLO por un escalamiento no
    #: tiene gestión. Rellenarlo con la hora del escalamiento mentiría — esta
    #: columna ancla la ventana de reincidencia y el filtro de reaparición, así
    #: que un escalamiento se contaría como gestión.
    #: SIN `default=_utcnow`, y no es un descuido: un default de Python dispara
    #: cuando el valor es `None`, así que un caso creado sólo por un
    #: escalamiento nacería con fecha de gestión y la bandeja lo mostraría
    #: "gestionado por" nadie. `mark_fault_managed` la escribe explícitamente.
    last_managed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_managed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    #: Escalamiento del ciclo VIGENTE. No hace falta limpiarlas al empezar un
    #: ciclo nuevo: la rama `escalada` exige `managed_through_at IS NULL`, así
    #: que un escalamiento deja de gobernar el estado en cuanto hay gestión. Lo
    #: que sí exige regla propia es el enlace publicado como dato: se oculta sólo
    #: cuando empezó un ciclo nuevo, o sea estado `pending` habiendo una gestión
    #: previa. Ver `_escalation_fields`.
    #: Ciclo vigente de la falla. Entra en la referencia `NF-…` que el taller
    #: copia en los trabajos, de modo que un trabajo marcado sólo puede
    #: pertenecer al ciclo que lo marcó. Avanza al ACTUAR sobre un ciclo nuevo,
    #: no cuando el ciclo nace: mover la referencia por el paso del tiempo
    #: dejaría obsoleta la que alguien ya copió.
    cycle_number: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    #: Orden de CloudFleet que SOSTIENE la gestión vigente. No es informativa:
    #: es lo que se vuelve a mirar para saber si la gestión sigue en pie.
    confirmed_work_order_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    escalated_novedad_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("novedades.id", ondelete="SET NULL"), nullable=True
    )
    escalated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    escalated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    last_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class NavifaultManagedFaultAction(Base):
    """Bitácora inmutable de cada gestión o reversión de un caso."""

    __tablename__ = "managed_fault_actions"
    __table_args__ = (
        CheckConstraint(
            "action_type IN ('managed', 'unmanaged', 'escalated')",
            name="ck_navifault_managed_action_type",
        ),
        Index("ix_navifault_managed_action_case_at", "case_id", "managed_at"),
        {"schema": NAVIFAULT_SCHEMA},
    )

    action_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{NAVIFAULT_SCHEMA}.managed_fault_cases.case_id", ondelete="CASCADE"),
        nullable=False,
    )
    action_type: Mapped[str] = mapped_column(String(24), nullable=False, default="managed")
    managed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    managed_through_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    managed_through_row_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    cycle_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Orden contra la que se confirmó esta gestión, y la COPIA de lo que el
    #: taller registró: trabajo, sistema, tipo de mantenimiento y repuestos. Se
    #: guarda la copia y no los identificadores porque anular una orden borra
    #: sus trabajos y repuestos, y la declaración quedaría apuntando a nada.
    work_order_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class NavifaultManagementConfiguration(Base):
    """Inicio auditable del seguimiento operativo de gestión Navifault."""

    __tablename__ = "management_configuration"
    __table_args__ = (
        CheckConstraint("singleton IS TRUE", name="ck_navifault_management_configuration_singleton"),
        {"schema": NAVIFAULT_SCHEMA},
    )

    singleton: Mapped[bool] = mapped_column(Boolean, primary_key=True, default=True)
    tracking_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
