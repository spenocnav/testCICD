"""Decisiones append-only sobre anomalías diarias de distancia."""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin


class DistanceQualityDecision(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "distance_quality_decisions"

    fact_row_id: Mapped[str] = mapped_column(String(128), nullable=False)
    analytics_vehicle_id: Mapped[str] = mapped_column(String(128), nullable=False)
    date_key: Mapped[int] = mapped_column(BigInteger, nullable=False)
    observation_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    observed_kms_ecm: Mapped[float | None]
    observed_kms_gps: Mapped[float | None]
    threshold_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)

    __table_args__ = (
        CheckConstraint(
            "origin IN ('automatic', 'manual')", name="ck_distance_decision_origin"
        ),
        CheckConstraint(
            "action IN ('use_ecm', 'use_gps', 'exclude', 'restore_auto')",
            name="ck_distance_decision_action",
        ),
        Index(
            "ix_distance_decision_fact_created",
            "fact_row_id",
            "created_at",
        ),
        Index(
            "ix_distance_decision_vehicle_date",
            "analytics_vehicle_id",
            "date_key",
        ),
    )
