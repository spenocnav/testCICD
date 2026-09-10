"""Constantes compartidas de RBAC (módulos, acciones, rol admin).

Nota: las migraciones Alembic NO importan de aquí (deben ser auto-contenidas,
un snapshot del estado). Este módulo es para uso en runtime (endpoints, servicios).
Si cambias el catálogo, refléjalo también en una migración.
"""

from __future__ import annotations

# Código del rol con bypass total de permisos.
ADMIN_ROLE_CODE = "admin"

# Acciones soportadas por módulo. `edit` implica `view`.
ACTION_VIEW = "view"
ACTION_EDIT = "edit"
ACTIONS: tuple[str, ...] = (ACTION_VIEW, ACTION_EDIT)


def perm_code(module_code: str, action: str) -> str:
    """Construye el código de permiso canónico: `<modulo>.<accion>`."""
    return f"{module_code}.{action}"
