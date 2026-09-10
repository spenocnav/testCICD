"""Auditoría de uso del portal. Solo administradores de plataforma.

Es vigilancia de actividad de personas, no consulta operativa: el mismo
criterio que el historial de calibración y el borrado de novedades. Por eso
exige rol `admin` y no un permiso de módulo.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import require_platform_admin
from app.db.session import get_db
from app.schemas.usage import UsageSummary, UsageUserDetail
from app.services import usage_service

router = APIRouter(
    prefix="/usage",
    tags=["usage"],
    dependencies=[Depends(require_platform_admin)],
)

_MAX_RANGE_DAYS = 400


def _resolve_range(start_date: date | None, end_date: date | None) -> tuple[date, date]:
    today = datetime.now(ZoneInfo(settings.reportes_timezone)).date()
    end = end_date or today
    start = start_date or (end - timedelta(days=29))
    if start > end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_date no puede ser posterior a end_date",
        )
    if (end - start).days > _MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Rango máximo: {_MAX_RANGE_DAYS} días",
        )
    return start, end


@router.get("/summary", response_model=UsageSummary)
async def usage_summary(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> UsageSummary:
    """Resumen del rango: usuarios más activos, secciones, flotas y rutas."""
    start, end = _resolve_range(start_date, end_date)
    return await usage_service.get_usage_summary(db, start, end)


@router.get("/users/{user_id}", response_model=UsageUserDetail)
async def usage_user_detail(
    user_id: uuid.UUID,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> UsageUserDetail:
    """Desglose de la actividad de un usuario: días, secciones, flotas, rutas."""
    start, end = _resolve_range(start_date, end_date)
    return await usage_service.get_usage_user_detail(db, user_id, start, end)
