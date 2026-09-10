"""índices para paths de autorización y mantenimiento

Revision ID: b2c3d4e5f6a8
Revises: a1b2c3d4e5f7
Create Date: 2026-07-14
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b2c3d4e5f6a8"
down_revision: str | None = "a1b2c3d4e5f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_vehicles_active_geotab_device_id",
        "vehicles",
        ["geotab_device_id"],
        postgresql_where=sa.text(
            "geotab_device_id IS NOT NULL AND is_active IS TRUE"
        ),
    )
    op.create_index(
        "ix_cf_wo_vehicle_completion",
        "cloudfleet_work_orders",
        ["vehicle_code", "technical_completion_date"],
    )
    op.create_index(
        "ix_cf_sched_vehicle_due",
        "cloudfleet_maintenance_schedules",
        ["vehicle_code", "date_to_execute"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_cf_sched_vehicle_due",
        table_name="cloudfleet_maintenance_schedules",
    )
    op.drop_index(
        "ix_cf_wo_vehicle_completion",
        table_name="cloudfleet_work_orders",
    )
    op.drop_index(
        "ix_vehicles_active_geotab_device_id",
        table_name="vehicles",
    )
