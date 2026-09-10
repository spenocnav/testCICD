"""Modelos ORM. Importar todos aquí para que Alembic los descubra."""

from app.models.calificacion_config import CalificacionConfigVersion
from app.models.cloudfleet import (
    CloudfleetMaintenanceSchedule,
    CloudfleetMeterSyncState,
    CloudfleetTrackingEvent,
    CloudfleetVehicle,
    CloudfleetWorkOrder,
)
from app.models.distance_quality import DistanceQualityDecision
from app.models.etl_trigger import EtlMicrobatchCommit, EtlTriggerRequest
from app.models.fleet import Fleet, user_fleets
from app.models.master_data import (
    GeotabCredential,
    GeotabDatabase,
    GeotabRule,
    GeotabRuleApplication,
    MotorCatalog,
    MotorRpmBand,
    MotorRule,
    RpmRule,
    SyncState,
    Vehicle,
    VehicleExtractionState,
)
from app.models.module import Module
from app.models.navifault import (
    NavifaultAsset,
    NavifaultCorpusImportRun,
    NavifaultDateplateManualMap,
    NavifaultEngineManualCandidate,
    NavifaultFaultAnalysis,
    NavifaultFaultAnalysisAsset,
    NavifaultFaultAnalysisDocument,
    NavifaultFaultAnalysisTable,
    NavifaultFaultPage,
    NavifaultFaultPageAnalysis,
    NavifaultFaultPageAsset,
    NavifaultFaultPageDocument,
    NavifaultFaultPageLanguageLink,
    NavifaultFaultPageTable,
    NavifaultFaultProtocolKey,
    NavifaultGeneratedClientDescription,
    NavifaultManagedFaultAction,
    NavifaultManagedFaultCase,
    NavifaultManual,
    NavifaultTechnicalDocument,
    NavifaultTechnicalDocumentAsset,
    NavifaultTechnicalDocumentLink,
    NavifaultTechnicalDocumentTable,
)
from app.models.novedad import Novedad, NovedadAttachment, NovedadOutbox
from app.models.permission import Permission
from app.models.refresh_token import RefreshToken
from app.models.role import Role, role_permissions
from app.models.sync_run import SyncRun
from app.models.usage import UsageEvent
from app.models.user import User, user_roles

__all__ = [
    "CalificacionConfigVersion",
    "CloudfleetMaintenanceSchedule",
    "CloudfleetMeterSyncState",
    "CloudfleetTrackingEvent",
    "CloudfleetVehicle",
    "CloudfleetWorkOrder",
    "DistanceQualityDecision",
    "EtlMicrobatchCommit",
    "EtlTriggerRequest",
    "Fleet",
    "GeotabCredential",
    "GeotabDatabase",
    "GeotabRule",
    "GeotabRuleApplication",
    "Module",
    "MotorCatalog",
    "MotorRpmBand",
    "MotorRule",
    "NavifaultAsset",
    "NavifaultCorpusImportRun",
    "NavifaultDateplateManualMap",
    "NavifaultEngineManualCandidate",
    "NavifaultFaultAnalysis",
    "NavifaultFaultAnalysisAsset",
    "NavifaultFaultAnalysisDocument",
    "NavifaultFaultAnalysisTable",
    "NavifaultFaultPage",
    "NavifaultFaultPageAnalysis",
    "NavifaultFaultPageAsset",
    "NavifaultFaultPageDocument",
    "NavifaultFaultPageLanguageLink",
    "NavifaultFaultPageTable",
    "NavifaultFaultProtocolKey",
    "NavifaultGeneratedClientDescription",
    "NavifaultManagedFaultAction",
    "NavifaultManagedFaultCase",
    "NavifaultManual",
    "NavifaultTechnicalDocument",
    "NavifaultTechnicalDocumentAsset",
    "NavifaultTechnicalDocumentLink",
    "NavifaultTechnicalDocumentTable",
    "Novedad",
    "NovedadAttachment",
    "NovedadOutbox",
    "Permission",
    "RefreshToken",
    "Role",
    "RpmRule",
    "SyncRun",
    "SyncState",
    "UsageEvent",
    "User",
    "Vehicle",
    "VehicleExtractionState",
    "role_permissions",
    "user_fleets",
    "user_roles",
]
