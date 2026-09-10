from __future__ import annotations

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.db.session import AsyncSessionLocal, _connect_args


def test_connect_args_support_psycopg_and_asyncpg() -> None:
    psycopg = _connect_args("postgresql+psycopg://user:password@db/app")
    asyncpg = _connect_args("postgresql+asyncpg://user:password@db/app")

    assert "options" in psycopg
    assert "statement_timeout" in psycopg["options"]
    assert "lock_timeout" in psycopg["options"]
    assert "idle_in_transaction_session_timeout" in psycopg["options"]
    assert asyncpg["server_settings"]["application_name"] == (settings.database_application_name)


@pytest.mark.asyncio
async def test_runtime_connection_has_defensive_timeouts() -> None:
    async with AsyncSessionLocal() as session:
        values = dict(
            (
                await session.execute(
                    text(
                        "SELECT name, setting FROM pg_settings WHERE name IN "
                        "('statement_timeout', 'lock_timeout', "
                        "'idle_in_transaction_session_timeout')"
                    )
                )
            ).all()
        )

    assert int(values["statement_timeout"]) == settings.database_statement_timeout_ms
    assert int(values["lock_timeout"]) == settings.database_lock_timeout_ms
    assert int(values["idle_in_transaction_session_timeout"]) == (
        settings.database_idle_transaction_timeout_ms
    )


@pytest.mark.asyncio
async def test_scalability_indexes_and_checks_exist() -> None:
    expected_indexes = {
        "ix_user_roles_role_user",
        "ix_role_permissions_permission_role",
        "ix_user_fleets_fleet_user",
        "ix_vehicles_fleet_plate_id",
        "ix_vehicles_active_fleet_plate_id",
        "ix_vehicle_extraction_pending",
        "ix_etl_trigger_request_pending",
        "ix_novedades_fleet_reported_id",
        "ix_novedad_outbox_due",
        "ix_novedad_outbox_stale_processing",
        "ix_cf_meter_state_status",
    }
    expected_checks = {
        "ck_vehicle_extraction_state_status",
        "ck_vehicle_extraction_state_version_nonnegative",
        "ck_etl_trigger_request_status",
        "ck_sync_run_kind",
        "ck_sync_run_trigger",
        "ck_sync_run_mode",
        "ck_sync_run_status",
        "ck_sync_run_duration_nonnegative",
        "ck_sync_run_time_order",
        "ck_cf_meter_state_type",
        "ck_cf_meter_state_status",
        "ck_cf_meter_state_value_nonnegative",
    }

    async with AsyncSessionLocal() as session:
        indexes = set(
            (
                await session.execute(
                    text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
                )
            ).scalars()
        )
        checks = set(
            (
                await session.execute(
                    text(
                        "SELECT conname FROM pg_constraint c "
                        "JOIN pg_namespace n ON n.oid = c.connamespace "
                        "WHERE n.nspname = 'public' AND c.contype = 'c'"
                    )
                )
            ).scalars()
        )

    assert expected_indexes <= indexes
    assert expected_checks <= checks
