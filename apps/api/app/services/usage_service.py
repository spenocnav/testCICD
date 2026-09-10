"""Consultas de la auditoría de uso sobre `usage_events`.

Todas las agregaciones excluyen las rutas de fontanería (`/me`, `/auth/*`):
son el latido de la sesión, no una acción del usuario, y contarlas infla las
cifras sin decir nada de qué hace la persona. `last_seen` sí las incluye,
porque para "última actividad" cualquier petición autenticada cuenta.

El rango se interpreta en la zona horaria de reportes (America/Bogota):
`start_date` a las 00:00 locales, `end_date` inclusive (hasta las 00:00 del
día siguiente).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.fleet import Fleet
from app.models.usage import UsageEvent
from app.models.user import User
from app.schemas.usage import (
    UsageDailyPoint,
    UsageFleetRow,
    UsageRouteRow,
    UsageSectionRow,
    UsageSummary,
    UsageUserDetail,
    UsageUserRow,
)

_PLUMBING_EXACT = ("/api/v1/me",)
_PLUMBING_PREFIX = "/api/v1/auth/"

_TOP_USERS = 100
_TOP_FLEETS = 50
_TOP_ROUTES = 30


def _tz() -> ZoneInfo:
    return ZoneInfo(settings.reportes_timezone)


def _range_utc(start_date: date, end_date: date) -> tuple[datetime, datetime]:
    tz = _tz()
    start = datetime.combine(start_date, time.min, tzinfo=tz)
    end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=tz)
    return start, end


def _local_day():
    """Expresión SQL: fecha local (zona de reportes) del evento."""
    return cast(func.timezone(settings.reportes_timezone, UsageEvent.ts), Date)


def _section_expr():
    """Segmento de módulo de la ruta: '/api/v1/reportes/…' -> 'reportes'."""
    return func.split_part(UsageEvent.route, "/", 4)


def _base_filters(start: datetime, end: datetime, user_id: uuid.UUID | None):
    conds = [
        UsageEvent.ts >= start,
        UsageEvent.ts < end,
        UsageEvent.route.notin_(_PLUMBING_EXACT),
        ~UsageEvent.route.like(f"{_PLUMBING_PREFIX}%"),
    ]
    if user_id is not None:
        conds.append(UsageEvent.user_id == user_id)
    return conds


async def _daily(
    db: AsyncSession, start: datetime, end: datetime, user_id: uuid.UUID | None
) -> list[UsageDailyPoint]:
    day = _local_day().label("day")
    stmt = (
        select(
            day,
            func.count().label("requests"),
            func.count(func.distinct(UsageEvent.user_id)).label("users"),
        )
        .where(*_base_filters(start, end, user_id))
        .group_by(day)
        .order_by(day)
    )
    rows = (await db.execute(stmt)).all()
    by_day = {r.day: UsageDailyPoint(day=r.day, requests=r.requests, users=r.users) for r in rows}
    first = start.astimezone(_tz()).date()
    last = (end - timedelta(microseconds=1)).astimezone(_tz()).date()
    return [
        by_day.get(day, UsageDailyPoint(day=day, requests=0, users=0))
        for day in (first + timedelta(days=i) for i in range((last - first).days + 1))
    ]


async def _sections(
    db: AsyncSession, start: datetime, end: datetime, user_id: uuid.UUID | None
) -> list[UsageSectionRow]:
    section = _section_expr().label("section")
    stmt = (
        select(
            section,
            func.count().label("requests"),
            func.count(func.distinct(UsageEvent.user_id)).label("users"),
        )
        .where(*_base_filters(start, end, user_id))
        .group_by(section)
        .order_by(func.count().desc())
    )
    rows = (await db.execute(stmt)).all()
    return [UsageSectionRow(section=r.section, requests=r.requests, users=r.users) for r in rows]


async def _fleets(
    db: AsyncSession, start: datetime, end: datetime, user_id: uuid.UUID | None
) -> list[UsageFleetRow]:
    inner = (
        select(
            UsageEvent.user_id.label("user_id"),
            func.unnest(UsageEvent.fleet_ids).label("fleet_id"),
        )
        .where(UsageEvent.fleet_ids.isnot(None), *_base_filters(start, end, user_id))
        .subquery()
    )
    stmt = (
        select(
            inner.c.fleet_id,
            Fleet.name.label("fleet_name"),
            func.count().label("requests"),
            func.count(func.distinct(inner.c.user_id)).label("users"),
        )
        .join(Fleet, Fleet.id == inner.c.fleet_id, isouter=True)
        .group_by(inner.c.fleet_id, Fleet.name)
        .order_by(func.count().desc())
        .limit(_TOP_FLEETS)
    )
    rows = (await db.execute(stmt)).all()
    return [
        UsageFleetRow(
            fleet_id=r.fleet_id, fleet_name=r.fleet_name, requests=r.requests, users=r.users
        )
        for r in rows
    ]


async def _routes(
    db: AsyncSession, start: datetime, end: datetime, user_id: uuid.UUID | None
) -> list[UsageRouteRow]:
    stmt = (
        select(
            UsageEvent.method,
            UsageEvent.route,
            func.count().label("requests"),
            func.avg(UsageEvent.duration_ms).label("avg_ms"),
            func.percentile_cont(0.95).within_group(UsageEvent.duration_ms.asc()).label("p95_ms"),
        )
        .where(*_base_filters(start, end, user_id))
        .group_by(UsageEvent.method, UsageEvent.route)
        .order_by(func.count().desc())
        .limit(_TOP_ROUTES)
    )
    rows = (await db.execute(stmt)).all()
    return [
        UsageRouteRow(
            method=r.method,
            route=r.route,
            requests=r.requests,
            avg_ms=round(float(r.avg_ms), 1),
            p95_ms=round(float(r.p95_ms), 1),
        )
        for r in rows
    ]


async def _user_lookup(db: AsyncSession, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, User]:
    if not user_ids:
        return {}
    rows = (await db.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()
    return {u.id: u for u in rows}


async def get_usage_summary(db: AsyncSession, start_date: date, end_date: date) -> UsageSummary:
    start, end = _range_utc(start_date, end_date)

    # Actividad por usuario. `last_seen` se calcula SIN excluir fontanería:
    # una pestaña abierta que refresca sesión es actividad real de la persona.
    user_stmt = (
        select(
            UsageEvent.user_id,
            func.count().label("requests"),
        )
        .where(*_base_filters(start, end, None))
        .group_by(UsageEvent.user_id)
        .order_by(func.count().desc())
        .limit(_TOP_USERS)
    )
    user_rows = (await db.execute(user_stmt)).all()

    last_seen_stmt = (
        select(UsageEvent.user_id, func.max(UsageEvent.ts).label("last_seen"))
        .where(UsageEvent.ts >= start, UsageEvent.ts < end)
        .group_by(UsageEvent.user_id)
    )
    last_seen = {r.user_id: r.last_seen for r in (await db.execute(last_seen_stmt)).all()}

    # Sección dominante por usuario (para la columna "qué hace").
    section = _section_expr().label("section")
    per_user_section_stmt = (
        select(UsageEvent.user_id, section, func.count().label("n"))
        .where(*_base_filters(start, end, None))
        .group_by(UsageEvent.user_id, section)
    )
    top_section: dict[uuid.UUID, tuple[str, int]] = {}
    for r in (await db.execute(per_user_section_stmt)).all():
        current = top_section.get(r.user_id)
        if current is None or r.n > current[1]:
            top_section[r.user_id] = (r.section, r.n)

    users_by_id = await _user_lookup(db, [r.user_id for r in user_rows])
    users = [
        UsageUserRow(
            user_id=r.user_id,
            email=users_by_id[r.user_id].email if r.user_id in users_by_id else None,
            full_name=users_by_id[r.user_id].full_name if r.user_id in users_by_id else None,
            is_active=users_by_id[r.user_id].is_active if r.user_id in users_by_id else None,
            requests=r.requests,
            last_seen=last_seen[r.user_id],
            top_section=top_section.get(r.user_id, (None, 0))[0],
        )
        for r in user_rows
    ]

    daily = await _daily(db, start, end, None)
    total_requests = sum(p.requests for p in daily)
    active_users_stmt = select(func.count(func.distinct(UsageEvent.user_id))).where(
        *_base_filters(start, end, None)
    )
    active_users = (await db.execute(active_users_stmt)).scalar_one()

    return UsageSummary(
        start_date=start_date,
        end_date=end_date,
        total_requests=total_requests,
        active_users=active_users,
        daily=daily,
        users=users,
        sections=await _sections(db, start, end, None),
        fleets=await _fleets(db, start, end, None),
        routes=await _routes(db, start, end, None),
    )


async def get_usage_user_detail(
    db: AsyncSession, user_id: uuid.UUID, start_date: date, end_date: date
) -> UsageUserDetail:
    start, end = _range_utc(start_date, end_date)

    daily = await _daily(db, start, end, user_id)
    last_seen_stmt = select(func.max(UsageEvent.ts)).where(
        UsageEvent.user_id == user_id, UsageEvent.ts >= start, UsageEvent.ts < end
    )
    last_seen = (await db.execute(last_seen_stmt)).scalar_one_or_none()
    user = (await _user_lookup(db, [user_id])).get(user_id)

    return UsageUserDetail(
        user_id=user_id,
        email=user.email if user else None,
        full_name=user.full_name if user else None,
        is_active=user.is_active if user else None,
        start_date=start_date,
        end_date=end_date,
        total_requests=sum(p.requests for p in daily),
        last_seen=last_seen,
        daily=daily,
        sections=await _sections(db, start, end, user_id),
        fleets=await _fleets(db, start, end, user_id),
        routes=await _routes(db, start, end, user_id),
    )
