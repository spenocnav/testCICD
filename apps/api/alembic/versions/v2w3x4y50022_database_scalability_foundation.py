"""base de escalabilidad, integridad e índices operativos

Revision ID: v2w3x4y50022
Revises: u1v2w3x40021
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "v2w3x4y50022"
down_revision: str | None = "u1v2w3x40021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _add_check_not_valid(table: str, name: str, expression: str) -> None:
    op.execute(
        f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({expression}) NOT VALID"
    )
    op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")


def upgrade() -> None:
    # NOT VALID separa la instalación de la validación y evita que PostgreSQL
    # sostenga un ACCESS EXCLUSIVE lock durante el scan de filas existentes.
    _add_check_not_valid(
        "vehicle_extraction_state",
        "ck_vehicle_extraction_state_status",
        "status IN ('pending', 'ok', 'error', 'failed')",
    )
    _add_check_not_valid(
        "vehicle_extraction_state",
        "ck_vehicle_extraction_state_version_nonnegative",
        "version >= 0",
    )
    _add_check_not_valid(
        "etl_trigger_request",
        "ck_etl_trigger_request_status",
        "status IN ('pending', 'running', 'done', 'error')",
    )
    _add_check_not_valid(
        "sync_run",
        "ck_sync_run_kind",
        "kind IN ('master', 'cloudfleet', 'reportes')",
    )
    _add_check_not_valid(
        "sync_run",
        "ck_sync_run_trigger",
        "trigger IN ('manual', 'worker', 'cli')",
    )
    _add_check_not_valid(
        "sync_run",
        "ck_sync_run_mode",
        "mode IN ('full', 'incremental')",
    )
    _add_check_not_valid(
        "sync_run",
        "ck_sync_run_status",
        "status IN ('success', 'partial', 'error')",
    )
    _add_check_not_valid(
        "sync_run",
        "ck_sync_run_duration_nonnegative",
        "duration_ms >= 0",
    )
    _add_check_not_valid(
        "sync_run",
        "ck_sync_run_time_order",
        "finished_at >= started_at",
    )

    # Los índices se construyen fuera de la transacción para no bloquear
    # escrituras de tablas activas durante un despliegue.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_user_roles_role_user "
            "ON user_roles (role_id, user_id)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_role_permissions_permission_role "
            "ON role_permissions (permission_id, role_id)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_user_fleets_fleet_user "
            "ON user_fleets (fleet_id, user_id)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_vehicles_fleet_plate_id "
            "ON vehicles (fleet_id, plate, id)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_vehicles_active_fleet_plate_id "
            "ON vehicles (fleet_id, plate, id) WHERE is_active IS TRUE"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_vehicle_extraction_pending "
            "ON vehicle_extraction_state (dataset, backfill_from, vehicle_id) "
            "WHERE status IN ('pending', 'error', 'failed')"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_etl_trigger_request_pending "
            "ON etl_trigger_request (created_at, id) WHERE status = 'pending'"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_novedades_fleet_reported_id "
            "ON novedades (fleet_id, reported_at, id)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_novedad_outbox_stale_processing "
            "ON novedad_outbox (locked_at, id) "
            "WHERE status = 'processing' AND locked_at IS NOT NULL"
        )

        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_vehicles_fleet_id")
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS ix_etl_trigger_request_status"
        )
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS ix_novedades_fleet_reported_at"
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_novedades_fleet_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_novedad_outbox_due")
        op.execute(
            "CREATE INDEX CONCURRENTLY ix_novedad_outbox_due "
            "ON novedad_outbox (status, available_at, id) "
            "WHERE status IN ('pending', 'failed')"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_novedad_outbox_due")
        op.execute(
            "CREATE INDEX CONCURRENTLY ix_novedad_outbox_due "
            "ON novedad_outbox (status, available_at, id)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_novedades_fleet_id "
            "ON novedades (fleet_id)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_novedades_fleet_reported_at "
            "ON novedades (fleet_id, reported_at)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_etl_trigger_request_status ON etl_trigger_request (status)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_vehicles_fleet_id "
            "ON vehicles (fleet_id)"
        )

        for index_name in (
            "ix_novedad_outbox_stale_processing",
            "ix_novedades_fleet_reported_id",
            "ix_etl_trigger_request_pending",
            "ix_vehicle_extraction_pending",
            "ix_vehicles_active_fleet_plate_id",
            "ix_vehicles_fleet_plate_id",
            "ix_user_fleets_fleet_user",
            "ix_role_permissions_permission_role",
            "ix_user_roles_role_user",
        ):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}")

    for table, constraint in (
        ("sync_run", "ck_sync_run_time_order"),
        ("sync_run", "ck_sync_run_duration_nonnegative"),
        ("sync_run", "ck_sync_run_status"),
        ("sync_run", "ck_sync_run_mode"),
        ("sync_run", "ck_sync_run_trigger"),
        ("sync_run", "ck_sync_run_kind"),
        ("etl_trigger_request", "ck_etl_trigger_request_status"),
        (
            "vehicle_extraction_state",
            "ck_vehicle_extraction_state_version_nonnegative",
        ),
        ("vehicle_extraction_state", "ck_vehicle_extraction_state_status"),
    ):
        op.drop_constraint(constraint, table_name=table, type_="check")
