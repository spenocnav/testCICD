from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.fleet import FleetRead
from app.schemas.role import RoleSummary

EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    is_active: bool
    is_archived: bool = False
    last_login_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    roles: list[RoleSummary] = []
    fleets: list[FleetRead] = []


class UserCreate(BaseModel):
    email: str = Field(pattern=EMAIL_PATTERN, max_length=320)
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=120)
    role_codes: list[str] = Field(default_factory=list)
    fleet_ids: list[uuid.UUID] = Field(default_factory=list)
    is_active: bool = True


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    is_active: bool | None = None
    role_codes: list[str] | None = None
    fleet_ids: list[uuid.UUID] | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)
    is_archived: bool | None = None


class PaginatedUsers(BaseModel):
    items: list[UserRead]
    total: int
    limit: int
    offset: int
