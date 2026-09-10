"""Réplica del estado real de las novedades en CloudFleet.

`novedades.cloudfleet_status` dice si el POST llegó (pending/sent/failed). No
dice si alguien atendió la novedad. Eso lo publica CloudFleet en cada issue
como `isDone`, `doneAt` y `workOrderDoneNumber`, y este servicio lo copia a
`novedades.external_*` para que la pantalla lo muestre sin llamar al
proveedor en cada render.

Cómo se consulta, y por qué así:

- Candidatas: novedades `sent` con número de issue cuyo `external_is_done`
  no sea TRUE. Una issue resuelta no se vuelve a abrir en CloudFleet, así
  que una vez marcada deja de costar llamadas.
- Con pocas candidatas (≤ `novedad_status_sync_list_threshold`) se consulta
  cada issue por número: `GET /issues/{n}`, una petición exacta por novedad.
- Con muchas se recorre el listado COMPLETO paginado
  (`GET /issues/?includeDone=true`, 50 por página) y se cruza por número.
  Completo porque los filtros del proveedor no sirven para acotarlo,
  verificado el 2026-08-28 contra la API real: `createdAtFrom` y
  `reportedAtFrom` se ignoran (devuelven desde la issue #1 de 2022) y
  `reportedBy` devuelve un subconjunto incompleto que no incluía una issue
  recién creada con ese mismo reportante. Lo que no aparezca en el listado se
  consulta una a una con techo (`novedad_status_sync_max_single_lookups`), y
  lo que siga sin aparecer queda en `missing` sin tocar la fila: no se
  inventa un estado.
- Presupuesto: 30 peticiones/min compartidas con los demás syncs; el cliente
  ya serializa y respeta `Retry-After`.
- CloudFleet no ofrece webhook de issues (sólo `work_order.*`), por eso se
  sondea desde la cadena diaria y no en tiempo real.

`external_synced_at` se escribe en toda candidata consultada, cambie o no:
es la fecha de la última verificación, no del último cambio.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select, update

from app.core.config import settings
from app.core.logging import get_logger
from app.db.advisory_lock import session_advisory_lock
from app.db.session import AsyncSessionLocal, engine
from app.models.navifault import NavifaultManagedFaultCase
from app.models.novedad import Novedad
from app.services import cloudfleet_service, sync_run_service
from app.services.cloudfleet_service import CloudfleetError

log = get_logger("novedad-status-sync")

LOCK_NAME = "navi-portal:novedad-status-sync"
SYNC_KIND = "novedades"

# CloudFleet serializa con siete decimales ("2019-06-13T09:30:00.0000000Z");
# `datetime.fromisoformat` acepta hasta seis.
_ISO_FRACTION = re.compile(r"^(?P<head>.*T\d{2}:\d{2}:\d{2})(?:\.(?P<frac>\d+))?(?P<tail>.*)$")


@dataclass(frozen=True)
class IssueState:
    is_done: bool
    done_at: datetime | None
    work_order_number: int | None
    #: Trabajo de la orden al que el taller ató esta novedad. CloudFleet lo
    #: publica como `associatedLabor: {id, name}`. Es lo que permite atribuir el
    #: desenlace al trabajo correcto: una orden puede tener varios y no todos
    #: son de esta novedad.
    associated_labor_id: int | None = None
    associated_labor_name: str | None = None


@dataclass(frozen=True)
class _Candidate:
    novedad_id: uuid.UUID
    issue_number: int
    created_at: datetime


def parse_cloudfleet_datetime(value: Any) -> datetime | None:
    """Convierte la fecha de CloudFleet a `datetime` UTC; `None` si no es legible."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    match = _ISO_FRACTION.match(text)
    if match:
        frac = match.group("frac")
        head = match.group("head")
        tail = match.group("tail")
        text = f"{head}.{frac[:6].ljust(6, '0')}{tail}" if frac else f"{head}{tail}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def issue_state(issue: Any) -> IssueState | None:
    """Extrae el estado de resolución de una issue; `None` si el objeto no lo trae."""
    if not isinstance(issue, dict):
        return None
    is_done = issue.get("isDone")
    if not isinstance(is_done, bool):
        return None
    work_order = issue.get("workOrderDoneNumber")
    labor = issue.get("associatedLabor")
    labor_id: int | None = None
    labor_name: str | None = None
    if isinstance(labor, dict):
        raw_id = labor.get("id")
        # El `id` ES el enlace: sin él no se puede ubicar el trabajo dentro de
        # la orden ni sus repuestos por `parts[].laborId`. Un nombre suelto
        # pintaría un trabajo asociado que nada puede resolver, así que la
        # asociación se descarta entera.
        if isinstance(raw_id, int):
            labor_id = raw_id
            raw_name = labor.get("name")
            labor_name = raw_name.strip() or None if isinstance(raw_name, str) else None
    return IssueState(
        is_done=is_done,
        done_at=parse_cloudfleet_datetime(issue.get("doneAt")),
        work_order_number=work_order if isinstance(work_order, int) else None,
        associated_labor_id=labor_id,
        associated_labor_name=labor_name,
    )


async def _load_candidates(*, escalated_only: bool = False) -> list[_Candidate]:
    """Novedades cuyo estado en CloudFleet todavía puede cambiar algo.

    Son dos conjuntos, y el segundo existe porque el primero resultó insuficiente.

    El primero es el histórico: una novedad enviada que no consta resuelta. Se
    apoyaba en que "una issue resuelta no se reabre en CloudFleet", y eso **es
    falso**: borrar el trabajo de la orden, o anular la orden, devuelve la
    novedad a abierta y le quita el número de orden. Comprobado el 2026-09-05
    sobre la novedad #706. Con sólo ese filtro, una novedad que se reabre queda
    congelada en el portal para siempre, porque dejó de mirarse al marcarse
    resuelta.

    El segundo es el que corrige eso, acotado: una novedad **escalada desde
    Navifault** se vigila mientras el ciclo de su falla siga abierto —es decir,
    hasta que alguien declare el desenlace—, cambie de estado las veces que
    cambie. Es un conjunto pequeño por construcción y es el único que gobierna
    el estado de una falla, así que es donde la corrección importa.

    Las novedades del módulo de novedades conservan la regla histórica: son
    cientos y no gobiernan nada.

    Con ``escalated_only`` se pide **sólo** el segundo conjunto. Lo usa el
    vigilante frecuente: sondear cada pocos minutos todas las novedades abiertas
    del módulo gastaría el presupuesto compartido de peticiones en filas que
    nadie está esperando, mientras que las escaladas son un puñado y gobiernan
    el estado de una falla en pantalla. La corrida diaria sigue mirando todo.
    """

    ciclo_abierto = (
        select(NavifaultManagedFaultCase.case_id)
        .where(
            NavifaultManagedFaultCase.escalated_novedad_id == Novedad.id,
            NavifaultManagedFaultCase.managed_through_at.is_(None),
        )
        .correlate(Novedad)
        .exists()
    )
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(Novedad.id, Novedad.cloudfleet_issue_number, Novedad.created_at)
                .where(
                    Novedad.cloudfleet_status == "sent",
                    Novedad.cloudfleet_issue_number.is_not(None),
                    ciclo_abierto
                    if escalated_only
                    else or_(
                        # Histórico: enviada y sin constar resuelta. Una borrada
                        # sigue entrando: el borrado es observación y no
                        # decisión, y dejar de mirarla lo convertiría en un
                        # cerrojo de una sola dirección que ningún 404
                        # transitorio podría deshacer.
                        Novedad.external_is_done.is_not(True),
                        # Escalada con el ciclo de su falla abierto: se vigila
                        # pase lo que pase, incluso ya resuelta, porque una
                        # novedad resuelta SÍ se reabre.
                        ciclo_abierto,
                    ),
                )
                .order_by(Novedad.created_at)
            )
        ).all()
    return [
        _Candidate(novedad_id=row[0], issue_number=int(row[1]), created_at=row[2])
        for row in rows
    ]


async def _apply_states(
    updates: list[dict[str, Any]],
) -> None:
    if not updates:
        return
    # "ORM bulk UPDATE by primary key": una sentencia executemany por forma,
    # cada dict con `id` más las columnas a escribir. Se agrupa por juego de
    # claves porque las formas NO son iguales: la fila de una issue borrada
    # escribe sólo la marca de borrado, y darle el juego completo pondría en
    # NULL el estado que sí se había verificado antes.
    grupos: dict[frozenset[str], list[dict[str, Any]]] = {}
    for row in updates:
        grupos.setdefault(frozenset(row), []).append(row)
    async with AsyncSessionLocal() as session:
        for lote in grupos.values():
            await session.execute(update(Novedad), lote)
        await session.commit()


async def _sync_core_unlocked(*, escalated_only: bool = False) -> dict[str, Any]:
    candidates = await _load_candidates(escalated_only=escalated_only)
    summary: dict[str, Any] = {
        "candidates": len(candidates),
        "listed": 0,
        "single_lookups": 0,
        "resolved_now": 0,
        "still_open": 0,
        "missing": 0,
        "missing_numbers": [],
        "deleted": 0,
        "deleted_numbers": [],
        "work_orders_checked": 0,
        # Distingue en `sync_run` la pasada del vigilante frecuente de la de la
        # cadena diaria: las dos se auditan con trigger `worker`.
        "escalated_only": escalated_only,
    }
    if not candidates:
        return summary

    by_number: dict[int, dict[str, Any]] = {}
    list_mode = len(candidates) > settings.novedad_status_sync_list_threshold
    if list_mode:
        issues = await cloudfleet_service.list_issues(include_done=True)
        summary["listed"] = len(issues)
        for listed_issue in issues:
            number = listed_issue.get("number")
            if isinstance(number, int):
                by_number[number] = listed_issue

    now = datetime.now(UTC)
    updates: list[dict[str, Any]] = []
    missing: list[int] = []
    deleted: list[int] = []
    lookups = 0
    ordenes_consultadas = 0
    # En modo directo cada candidata vale una consulta (ya acotado por el
    # umbral); en modo listado el techo protege el presupuesto compartido.
    max_lookups = (
        settings.novedad_status_sync_max_single_lookups if list_mode else len(candidates)
    )
    for candidate in candidates:
        issue: dict[str, Any] | None = by_number.get(candidate.issue_number)
        if issue is None and lookups < max_lookups:
            lookups += 1
            lookup = await cloudfleet_service.lookup_issue(candidate.issue_number)
            if not lookup.exists:
                # 404 explícito: CloudFleet ya no conoce la issue. Ausencia del
                # LISTADO no vale como evidencia —`includeDone=true` devuelve
                # sólo las resueltas, así que toda issue abierta falta de él—;
                # sólo la consulta por número lo demuestra.
                deleted.append(candidate.issue_number)
                updates.append(
                    {
                        "id": candidate.novedad_id,
                        "external_deleted_at": now,
                        "external_synced_at": now,
                    }
                )
                continue
            issue = lookup.payload
        state = issue_state(issue)
        if state is None:
            missing.append(candidate.issue_number)
            continue
        # El estado de la ORDEN es lo único que distingue "el taller la tiene"
        # de "el taller terminó". `is_done` no sirve: se activa al asignar la
        # novedad a una orden, con la orden todavía abierta. Se consulta sólo
        # cuando la novedad declara una, así que cuesta una petición por novedad
        # enlazada y ninguna por las demás.
        work_order_status: str | None = None
        if state.work_order_number is not None:
            ordenes_consultadas += 1
            orden = await cloudfleet_service.get_work_order(state.work_order_number)
            if isinstance(orden, dict):
                crudo = orden.get("status")
                work_order_status = crudo if isinstance(crudo, str) else None
        updates.append(
            {
                "id": candidate.novedad_id,
                "external_is_done": state.is_done,
                "external_done_at": state.done_at,
                "external_work_order_number": state.work_order_number,
                "external_work_order_status": work_order_status,
                "associated_labor_id": state.associated_labor_id,
                "associated_labor_name": state.associated_labor_name,
                # La issue está: si figuraba como borrada, se desmarca. El
                # borrado es observación y no decisión, así que un 404
                # transitorio del proveedor se corrige en la corrida siguiente.
                "external_deleted_at": None,
                "external_synced_at": now,
            }
        )
        if state.is_done:
            summary["resolved_now"] += 1
        else:
            summary["still_open"] += 1

    await _apply_states(updates)
    summary["single_lookups"] = lookups
    summary["missing"] = len(missing)
    summary["missing_numbers"] = missing[:50]
    summary["deleted"] = len(deleted)
    summary["deleted_numbers"] = deleted[:50]
    summary["work_orders_checked"] = ordenes_consultadas
    return summary


async def run_novedad_status_sync(
    *,
    trigger: str = "cli",
    actor_user_id: uuid.UUID | None = None,
    actor_email: str | None = None,
    escalated_only: bool = False,
) -> dict[str, Any]:
    """Ejecuta una pasada y la registra en `sync_run` (kind `novedades`).

    Toma su propio advisory lock: el disparo manual, el paso diario y el
    vigilante frecuente no se pisan; el segundo en llegar recibe
    `OperationAlreadyRunningError`.

    Con ``escalated_only`` mira sólo las novedades escaladas cuyo ciclo sigue
    abierto. Es lo que corre cada pocos minutos; la cadena diaria no lo pasa y
    revisa todo.
    """
    if settings.cloudfleet_id_reportedby is None:
        raise CloudfleetError("CLOUDFLEET_ID_REPORTEDBY no está configurado")
    mode = "incremental"
    async with session_advisory_lock(engine, LOCK_NAME):
        started_at = datetime.now(UTC)
        run_id = await sync_run_service.start(
            kind=SYNC_KIND,
            trigger=trigger,
            mode=mode,
            started_at=started_at,
            actor_user_id=actor_user_id,
            actor_email=actor_email,
        )
        try:
            summary = await _sync_core_unlocked(escalated_only=escalated_only)
        except Exception as exc:
            await sync_run_service.finish(
                run_id,
                kind=SYNC_KIND,
                trigger=trigger,
                mode=mode,
                status="error",
                started_at=started_at,
                finished_at=datetime.now(UTC),
                error=str(exc),
                actor_user_id=actor_user_id,
                actor_email=actor_email,
            )
            raise
        await sync_run_service.finish(
            run_id,
            kind=SYNC_KIND,
            trigger=trigger,
            mode=mode,
            status="partial" if summary["missing"] else "success",
            started_at=started_at,
            finished_at=datetime.now(UTC),
            result=summary,
            actor_user_id=actor_user_id,
            actor_email=actor_email,
        )
        log.info("novedad_status_sync_done", **summary)
        return summary
