from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.db.session import get_db
from app.schemas.auth import MeResponse
from app.schemas.fleet import FleetRead
from app.schemas.user import UserRead
from app.services import fleet_service
from app.services.rbac import get_user_permission_codes, user_is_admin

router = APIRouter(tags=["me"])


@router.get("/me", response_model=MeResponse)
async def get_me(user: CurrentUser, db: AsyncSession = Depends(get_db)) -> MeResponse:
    fleets = await fleet_service.accessible_fleets(db, user)
    with_vehicles = await fleet_service.fleet_ids_with_active_vehicles(
        db, [f.id for f in fleets]
    )
    fleet_reads: list[FleetRead] = []
    for f in fleets:
        fr = FleetRead.model_validate(f)
        fr.has_vehicles = f.id in with_vehicles
        fleet_reads.append(fr)
    return MeResponse(
        user=UserRead.model_validate(user),
        permissions=sorted(get_user_permission_codes(user)),
        fleets=fleet_reads,
        fleet_scope_global=user_is_admin(user),
    )
