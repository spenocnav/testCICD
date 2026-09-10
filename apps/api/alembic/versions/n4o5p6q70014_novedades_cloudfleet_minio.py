"""tablas de novedades y adjuntos

Revision ID: n4o5p6q70014
Revises: m3n4o5p60013
Create Date: 2026-06-30
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "n4o5p6q70014"
down_revision: str | None = "m3n4o5p60013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "novedades",
        sa.Column("vehicle_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("fleet_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("vehicle_code", sa.String(), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reported_by_id", sa.Integer(), nullable=False),
        sa.Column("priority", sa.String(), nullable=False),
        sa.Column("odometer", sa.Numeric(14, 2), nullable=True),
        sa.Column("responsible_id", sa.Integer(), nullable=True),
        sa.Column("associated_labor_id", sa.Integer(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("send_mail", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cloudfleet_status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("cloudfleet_issue_number", sa.Integer(), nullable=True),
        sa.Column("cloudfleet_error", sa.Text(), nullable=True),
        sa.Column("cloudfleet_response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "cloudfleet_status IN ('pending', 'sent', 'failed')",
            name="ck_novedades_cloudfleet_status",
        ),
        sa.CheckConstraint(
            "priority IN ('low', 'medium', 'high')",
            name="ck_novedades_priority",
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["fleet_id"], ["fleets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_novedades_created_by_id", "novedades", ["created_by_id"])
    op.create_index("ix_novedades_fleet_id", "novedades", ["fleet_id"])
    op.create_index(
        "ix_novedades_fleet_reported_at", "novedades", ["fleet_id", "reported_at"]
    )
    op.create_index("ix_novedades_vehicle_code", "novedades", ["vehicle_code"])
    op.create_index("ix_novedades_vehicle_id", "novedades", ["vehicle_id"])

    op.create_table(
        "novedad_attachments",
        sa.Column("novedad_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bucket", sa.String(), nullable=False),
        sa.Column("object_key", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["novedad_id"], ["novedades.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_novedad_attachments_novedad_id", "novedad_attachments", ["novedad_id"]
    )
    op.create_index(
        "ix_novedad_attachments_object_key",
        "novedad_attachments",
        ["object_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_novedad_attachments_object_key", table_name="novedad_attachments")
    op.drop_index("ix_novedad_attachments_novedad_id", table_name="novedad_attachments")
    op.drop_table("novedad_attachments")

    op.drop_index("ix_novedades_vehicle_id", table_name="novedades")
    op.drop_index("ix_novedades_vehicle_code", table_name="novedades")
    op.drop_index("ix_novedades_fleet_reported_at", table_name="novedades")
    op.drop_index("ix_novedades_fleet_id", table_name="novedades")
    op.drop_index("ix_novedades_created_by_id", table_name="novedades")
    op.drop_table("novedades")
