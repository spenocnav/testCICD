"""Chequeos de Control de Acceso Basado en Roles."""

from __future__ import annotations

from collections.abc import Iterable

from app.core.rbac_constants import ACTION_EDIT, ACTION_VIEW, ADMIN_ROLE_CODE, perm_code
from app.models.role import Role
from app.models.user import User


def user_is_admin(user: User) -> bool:
    """True si el usuario tiene el rol Administrador (bypass total de permisos)."""
    return any(r.code == ADMIN_ROLE_CODE for r in user.roles)


def get_role_permission_codes(roles: Iterable[Role]) -> set[str]:
    """Códigos de permiso efectivos de una colección de roles.

    `edit` implica `view`: si un rol tiene `<modulo>.edit`, también se considera
    concedido `<modulo>.view`.
    """
    codes: set[str] = set()
    for role in roles:
        for perm in role.permissions:
            codes.add(perm.code)
            if perm.code.endswith(f".{ACTION_EDIT}"):
                module = perm.code.rsplit(".", 1)[0]
                codes.add(perm_code(module, ACTION_VIEW))
    return codes


def get_user_permission_codes(user: User) -> set[str]:
    """Códigos de permiso efectivos del usuario (unión de sus roles)."""
    return get_role_permission_codes(user.roles)


def user_has_permission(user: User, permission_code: str) -> bool:
    if not user.is_active:
        return False
    if user_is_admin(user):
        return True
    return permission_code in get_user_permission_codes(user)


def user_can(user: User, module_code: str, action: str) -> bool:
    """Atajo: ¿puede el usuario realizar `action` sobre `module_code`?"""
    return user_has_permission(user, perm_code(module_code, action))


def user_has_role(user: User, role_code: str) -> bool:
    return any(r.code == role_code for r in user.roles)
