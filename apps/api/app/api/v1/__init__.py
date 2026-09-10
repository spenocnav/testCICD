from fastapi import APIRouter

from app.api.v1 import (
    auth,
    data_quality,
    fleets,
    mantenimiento,
    me,
    modules,
    navifault,
    novedades,
    permissions,
    reportes,
    reportes_ralenti,
    roles,
    usage,
    users,
    vehicles,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(data_quality.router)
api_router.include_router(me.router)
api_router.include_router(users.router)
api_router.include_router(roles.router)
api_router.include_router(permissions.router)
api_router.include_router(modules.router)
api_router.include_router(novedades.router)
api_router.include_router(navifault.router)
api_router.include_router(fleets.router)
api_router.include_router(reportes.router)
api_router.include_router(reportes_ralenti.router)
api_router.include_router(mantenimiento.router)
api_router.include_router(vehicles.router)
api_router.include_router(usage.router)
