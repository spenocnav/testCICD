"""Tests de RBAC contra DB seeded.

Requiere `alembic upgrade head` ejecutado.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.models.module import Module
from app.models.permission import Permission
from app.models.role import Role
from app.services.rbac import (
    get_user_permission_codes,
    user_has_permission,
    user_has_role,
)
from app.services.user_service import (
    create_user,
    get_user_by_email,
    list_users,
)


@pytest.mark.asyncio
async def test_seeded_roles_have_expected_permissions() -> None:
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Role).options(selectinload(Role.permissions))
            )
            roles_by_code = {r.code: r for r in result.scalars().all()}

            admin = roles_by_code["admin"]
            navitrans = roles_by_code["admin_flota_navitrans"]
            viewer = roles_by_code["viewer"]

            admin_codes = {p.code for p in admin.permissions}
            navitrans_codes = {p.code for p in navitrans.permissions}
            viewer_codes = {p.code for p in viewer.permissions}

            legacy_permissions = await db.execute(
                select(Permission).where(Permission.code.in_(["reports.view", "reports.edit"]))
            )
            legacy_module = await db.execute(
                select(Module).where(Module.code == "reports")
            )
            assert legacy_permissions.scalars().first() is None
            assert legacy_module.scalar_one_or_none() is None

            assert "users.edit" in admin_codes
            assert "roles.edit" in admin_codes
            assert "mantenimiento.edit" in admin_codes
            assert "navifault_management.edit" in admin_codes
            # Navitrans gestiona flota pero no edita usuarios ni roles.
            assert "flotas.edit" in navitrans_codes
            assert "mantenimiento.edit" in navitrans_codes
            assert "users.edit" not in navitrans_codes
            assert "roles.edit" not in navitrans_codes
            assert "navifault_management.edit" not in navitrans_codes
            # Visor: solo lectura en módulos operativos.
            assert viewer_codes == {
                "users.view",
                "flotas.view",
                "reportes.view",
                "documents.view",
                "mantenimiento.view",
                "calidad_datos.view",
            }
    except (OSError, ConnectionError) as exc:
        pytest.skip(f"DB no disponible: {exc}")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_bootstrap_admin_has_full_permissions() -> None:
    try:
        async with AsyncSessionLocal() as db:
            admin = await get_user_by_email(db, settings.bootstrap_admin_email)
            assert admin is not None, "Bootstrap admin no fue creado por la migración seed"
            assert admin.is_active

            codes = get_user_permission_codes(admin)
            assert "users.edit" in codes
            assert "roles.edit" in codes
            assert user_has_permission(admin, "users.edit")
            assert user_has_role(admin, "admin")
    except (OSError, ConnectionError) as exc:
        pytest.skip(f"DB no disponible: {exc}")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_viewer_user_lacks_delete_permission() -> None:
    """Crea un usuario viewer ad-hoc y verifica que NO tenga permisos elevados."""
    try:
        import uuid as _uuid

        async with AsyncSessionLocal() as db:
            unique = _uuid.uuid4().hex[:8]
            user = await create_user(
                db,
                email=f"viewer-{unique}@portalclientes.test",
                password="ViewerPass123!",
                full_name="Viewer Test",
                role_codes=["viewer"],
            )
            await db.commit()

            # Recargar con roles+permisos (selectin ya configurado en el modelo)
            fresh = await get_user_by_email(db, user.email)
            assert fresh is not None
            assert user_has_permission(fresh, "users.view")
            assert not user_has_permission(fresh, "users.edit")
            assert not user_has_permission(fresh, "roles.edit")
            assert user_has_role(fresh, "viewer")

            # Cleanup
            await db.delete(fresh)
            await db.commit()
    except (OSError, ConnectionError) as exc:
        pytest.skip(f"DB no disponible: {exc}")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_inactive_user_loses_all_permissions() -> None:
    try:
        async with AsyncSessionLocal() as db:
            admin = await get_user_by_email(db, settings.bootstrap_admin_email)
            assert admin is not None
            original_state = admin.is_active
            admin.is_active = False
            assert not user_has_permission(admin, "users.edit")
            admin.is_active = original_state
    except (OSError, ConnectionError) as exc:
        pytest.skip(f"DB no disponible: {exc}")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_list_users_pagination_and_search() -> None:
    try:
        async with AsyncSessionLocal() as db:
            _users, total = await list_users(db, limit=10, offset=0)
            assert total >= 1

            # Búsqueda parcial por email del admin (robusta ante acumulación de
            # usuarios: el admin puede no estar en la primera página por fecha).
            users_q, total_q = await list_users(
                db,
                search=settings.bootstrap_admin_email.split("@")[0],
                limit=10,
            )
            assert total_q >= 1
            assert any(u.email == settings.bootstrap_admin_email for u in users_q)
    except (OSError, ConnectionError) as exc:
        pytest.skip(f"DB no disponible: {exc}")
    finally:
        await engine.dispose()
