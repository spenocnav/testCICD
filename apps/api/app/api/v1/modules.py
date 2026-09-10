from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_permission
from app.db.session import get_db
from app.models.module import Module
from app.schemas.module import ModuleRead

router = APIRouter(prefix="/modules", tags=["modules"])


@router.get(
    "",
    response_model=list[ModuleRead],
    dependencies=[Depends(require_permission("roles.view"))],
)
async def list_modules(db: AsyncSession = Depends(get_db)) -> list[ModuleRead]:
    result = await db.execute(
        select(Module).where(Module.is_active.is_(True)).order_by(Module.order, Module.name)
    )
    return [ModuleRead.model_validate(m) for m in result.scalars().all()]
