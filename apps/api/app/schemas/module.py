from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict


class ModuleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None = None
    order: int
    is_active: bool
