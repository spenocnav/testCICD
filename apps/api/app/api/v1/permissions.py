from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_permission
from app.db.session import get_db
from app.models.permission import Permission
from app.schemas.permission import PermissionRead

router = APIRouter(prefix="/permissions", tags=["permissions"])


@router.get(
    "",
    response_model=list[PermissionRead],
    dependencies=[Depends(require_permission("roles.view"))],
)
async def list_permissions(db: AsyncSession = Depends(get_db)) -> list[PermissionRead]:
    result = await db.execute(select(Permission).order_by(Permission.code))
    return [PermissionRead.model_validate(p) for p in result.scalars().all()]
