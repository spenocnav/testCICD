from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.fleet import FleetRead
from app.schemas.user import UserRead


class LoginRequest(BaseModel):
    # El formato no se valida en login: cualquier identificador no reconocido
    # debe terminar en el mismo error de credenciales inválidas.
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=128)


class SessionInfo(BaseModel):
    """Datos de sesión devueltos en login/refresh/me."""

    user: UserRead
    permissions: list[str]
    # Flotas accesibles por el usuario (todas si es admin / scope global).
    fleets: list[FleetRead] = []
    # True si el usuario ve todas las flotas (admin). El front muestra "Todas".
    fleet_scope_global: bool = False


class LoginResponse(SessionInfo):
    pass


class MeResponse(SessionInfo):
    pass
