from __future__ import annotations

import re
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.permission import PermissionRead

CODE_PATTERN = r"^[a-z][a-z0-9_]{1,63}$"


class RoleSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str


class RoleRead(RoleSummary):
    description: str | None = None
    is_system: bool
    permissions: list[PermissionRead] = []


class RoleCreate(BaseModel):
    code: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=255)
    permission_codes: list[str] = Field(default_factory=list)

    @field_validator("code")
    @classmethod
    def _valid_code(cls, v: str) -> str:
        v = v.strip().lower()
        if not re.match(CODE_PATTERN, v):
            raise ValueError("code debe ser minúsculas, empezar con letra y usar [a-z0-9_]")
        return v


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=255)


class RolePermissionsUpdate(BaseModel):
    permission_codes: list[str] = Field(default_factory=list)
