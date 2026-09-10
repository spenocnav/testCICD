from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict


class PermissionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    description: str | None = None
    resource: str | None = None
    action: str | None = None
