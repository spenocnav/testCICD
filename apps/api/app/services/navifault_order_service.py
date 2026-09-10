"""Qué registró el taller para una falla, dentro de una orden de CloudFleet.

Gestionar una falla es confirmar este resultado, no escribirlo. La fuente de
verdad de lo que se hizo es el registro del taller, y Navifault sólo lo lee.

El vínculo entre una falla y los trabajos que la resolvieron **no existe en la
API de CloudFleet**: la interfaz lo pinta con un distintivo pero no lo publica
en ninguna de las dos direcciones, y además una novedad sólo admite un trabajo.
Lo sustituye la referencia `NF-…`, que el taller copia en la descripción de cada
trabajo que corresponda. Copiarla es un acto deliberado de una persona, así que
un trabajo que la lleva es una declaración, no una coincidencia de texto.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.navifault import NavifaultManagedFaultCase
from app.models.user import User
from app.services import cloudfleet_service
from app.services.navifault_management_service import (
    MANAGEMENT_TIME_WINDOW_DAYS,
    WORK_ORDER_FINISHED_STATUSES,
    ManagedFaultSignature,
    ManagementFaultContext,
    advance_cycle_if_needed,
    context_for_case,
    is_telematics_fault,
    lock_or_create_case,
    mark_fault_managed,
    navifault_reference,
    unmark_fault_managed,
)
from app.services.novedad_status_sync_service import parse_cloudfleet_datetime

log = get_logger("navifault-order")


class NavifaultOrderError(RuntimeError):
    """La orden no sirve para confirmar esta falla, y el motivo es del usuario."""


@dataclass(frozen=True)
class OrderPart:
    """Repuesto consumido por un trabajo.

    `code` llega vacío en CloudFleet y la referencia de la pieza viaja dentro de
    `name`, así que el nombre es el dato y no un adorno.
    """

    external_id: int | None
    name: str | None
    code: str | None
    quantity: float | None


@dataclass(frozen=True)
class OrderLabor:
    """Trabajo de la orden que lleva la referencia de esta falla."""

    external_id: int | None
    name: str | None
    code: str | None
    system: str | None
    subsystem: str | None
    maintenance_type: str | None
    created_at: datetime | None
    parts: list[OrderPart] = field(default_factory=list)

    @property
    def replaced_component(self) -> bool:
        """Con repuestos se cambió una pieza; sin ellos se intervino sin cambiarla.

        Sale del dato real y no de lo que alguien escribió, que es lo que este
        modelo vino a corregir.
        """

        return bool(self.parts)


@dataclass(frozen=True)
class OrderMatch:
    """Lo que la orden aporta a esta falla."""

    work_order_number: int
    status: str | None
    reference: str
    labors: list[OrderLabor]

    @property
    def has_work(self) -> bool:
        return bool(self.labors)

    @property
    def is_finished(self) -> bool:
        """¿El taller terminó esta orden?

        Única autoridad de la regla: la consultan el guard de la confirmación y
        el contrato que la pantalla obedece. Un estado desconocido cuenta como
        NO terminada —sin saberlo no se puede afirmar que terminó—, el mismo
        fail-closed del guard del escalamiento.
        """

        return self.status in WORK_ORDER_FINISHED_STATUSES

    def snapshot(self) -> dict[str, Any]:
        """Copia de lo confirmado, para guardar en la bitácora.

        Se guarda la copia y no los identificadores porque anular una orden
        borra sus trabajos y repuestos: con sólo el id, la gestión quedaría
        apuntando a nada.
        """

        return {
            "work_order_number": self.work_order_number,
            "work_order_status": self.status,
            "reference": self.reference,
            "labors": [
                {
                    "id": labor.external_id,
                    "name": labor.name,
                    "code": labor.code,
                    "system": labor.system,
                    "subsystem": labor.subsystem,
                    "maintenance_type": labor.maintenance_type,
                    "replaced_component": labor.replaced_component,
                    "parts": [
                        {"id": p.external_id, "name": p.name, "code": p.code, "qty": p.quantity}
                        for p in labor.parts
                    ],
                }
                for labor in self.labors
            ],
        }


def _texto(valor: Any) -> str | None:
    if not isinstance(valor, str):
        return None
    limpio = valor.strip()
    return limpio or None


def _anidado(valor: Any, clave: str) -> str | None:
    return _texto(valor.get(clave)) if isinstance(valor, dict) else None


def _numero(valor: Any) -> float | None:
    return float(valor) if isinstance(valor, int | float) and not isinstance(valor, bool) else None


def _partes_por_trabajo(payload: dict[str, Any]) -> dict[int, list[OrderPart]]:
    """Agrupa los repuestos por el trabajo que los consumió.

    `parts[].laborId` es lo que hace utilizable el detalle: con varios trabajos
    en la misma orden, es lo único que dice qué pieza entró a cuál.
    """

    agrupados: dict[int, list[OrderPart]] = {}
    for crudo in payload.get("parts") or []:
        if not isinstance(crudo, dict):
            continue
        labor_id = crudo.get("laborId")
        if not isinstance(labor_id, int):
            continue
        agrupados.setdefault(labor_id, []).append(
            OrderPart(
                external_id=crudo.get("id") if isinstance(crudo.get("id"), int) else None,
                name=_texto(crudo.get("name")),
                code=_texto(crudo.get("code")),
                quantity=_numero(crudo.get("qty")),
            )
        )
    return agrupados


async def find_fault_work(
    *,
    vehicle_id: uuid.UUID,
    signature: ManagedFaultSignature,
    cycle_number: int,
    work_order_number: int,
) -> OrderMatch:
    """Trabajos de la orden que corresponden a ESTA falla y a ESTE ciclo.

    Un solo filtro, y basta: la referencia lleva el ciclo dentro, así que un
    trabajo marcado sólo puede pertenecer al ciclo que lo marcó. Con una
    referencia estable de por vida harían falta además una ventana de fechas y
    un registro de trabajos ya usados, y las dos dejaban huecos —una orden
    vieja lleva la misma marca; un ciclo abierto hace mucho acepta trabajo
    tardío—. El identificador los cierra por construcción.
    """

    reference = navifault_reference(
        vehicle_id=vehicle_id, signature=signature, cycle_number=cycle_number
    )
    payload = await cloudfleet_service.get_work_order(work_order_number)
    if payload is None:
        raise NavifaultOrderError(f"CloudFleet no conoce la orden {work_order_number}")

    partes = _partes_por_trabajo(payload)
    labors: list[OrderLabor] = []
    for crudo in payload.get("labors") or []:
        if not isinstance(crudo, dict):
            continue
        if reference not in (crudo.get("comment") or ""):
            continue
        created_at = parse_cloudfleet_datetime(crudo.get("createdAt"))
        labor_id = crudo.get("id") if isinstance(crudo.get("id"), int) else None
        labors.append(
            OrderLabor(
                external_id=labor_id,
                name=_texto(crudo.get("name")),
                code=_texto(crudo.get("code")),
                system=_anidado(crudo.get("system"), "name"),
                subsystem=_anidado(crudo.get("subsystem"), "name"),
                maintenance_type=_anidado(crudo.get("maintenanceType"), "name"),
                created_at=created_at,
                parts=partes.get(labor_id, []) if labor_id is not None else [],
            )
        )

    return OrderMatch(
        work_order_number=work_order_number,
        status=_texto(payload.get("status")),
        reference=reference,
        labors=labors,
    )


class NavifaultOrderNotFinishedError(RuntimeError):
    """La orden todavía no ha terminado, así que no hay nada que confirmar.

    Se separa de :class:`NavifaultConfirmationError` porque **no es una petición
    inválida**: está bien formada y la misma petición funcionará cuando el taller
    cierre. Es un conflicto con el estado, y por eso sale como 409 y no como 422
    —la misma distinción que se hizo entre validación y conflicto en el
    desenlace—.
    """


class NavifaultConfirmationError(RuntimeError):
    """No hay nada que confirmar, y el motivo es accionable por el usuario."""


async def preview_fault_work(
    session: AsyncSession, *, context: ManagementFaultContext, work_order_number: int
) -> OrderMatch:
    """Qué registró el taller para esta falla en esa orden, sin confirmar nada.

    Consultar la orden es una ACCIÓN sobre la falla, así que aquí es donde el
    ciclo puede avanzar: si la falla ya está en uno nuevo, la referencia que se
    busca es la de ese ciclo y no la del anterior.
    """

    case = await lock_or_create_case(session, context=context)
    cycle_number = await advance_cycle_if_needed(session, case=case, context=context)
    return await find_fault_work(
        vehicle_id=context.vehicle.id,
        signature=context.signature,
        cycle_number=cycle_number,
        work_order_number=work_order_number,
    )


async def confirm_from_work_order(
    session: AsyncSession,
    *,
    context: ManagementFaultContext,
    actor: User,
    work_order_number: int,
    note: str | None = None,
) -> tuple[OrderMatch, NavifaultManagedFaultCase]:
    """Gestiona la falla confirmando lo que el taller registró en esa orden.

    **Sin trabajo marcado no hay gestión**, y no porque una validación lo
    prohíba sino porque no hay nada que confirmar. Ése es el bloqueo que hace
    que el portal no pueda afirmar nada que la orden no diga.
    """

    encontrado = await preview_fault_work(
        session, context=context, work_order_number=work_order_number
    )
    # Gestionar es confirmar lo que el taller TERMINÓ. Con la orden abierta el
    # registro todavía puede cambiar —faltan trabajos, repuestos o el propio
    # diagnóstico—, así que confirmar ahí declararía resuelto algo que sigue en
    # curso y, de paso, desbloquearía un escalamiento nuevo sobre trabajo vivo.
    #
    # `status` es el estado que la orden tiene AHORA, leído en esta misma
    # consulta. Un estado desconocido no pasa: sin saberlo no se puede afirmar
    # que terminó, el mismo fail-closed que usa el guard del escalamiento.
    if not encontrado.is_finished:
        raise NavifaultOrderNotFinishedError(
            f"La orden {work_order_number} todavía no ha terminado"
            + (f" (está en «{encontrado.status}»)" if encontrado.status else "")
            + ". Una falla se gestiona cuando el taller cierra su orden o la marca en "
            "cierre técnico; hazlo en CloudFleet y vuelve a confirmar."
        )
    if not encontrado.has_work:
        raise NavifaultConfirmationError(
            f"La orden {work_order_number} no tiene ningún trabajo con la referencia "
            f"{encontrado.reference}. Cópiala en la descripción del trabajo que "
            "corresponda a esta falla y vuelve a intentarlo."
        )

    case, _accion = await mark_fault_managed(
        session,
        context=context,
        actor=actor,
        note=note,
        work_order_number=work_order_number,
        details=encontrado.snapshot(),
    )
    return encontrado, case

async def close_telematics_fault(
    session: AsyncSession,
    *,
    context: ManagementFaultContext,
    actor: User,
    note: str,
) -> NavifaultManagedFaultCase:
    """Cierra con una nota una falla del propio equipo telemático.

    Es la única excepción al "sin orden no hay gestión", y existe porque estas
    fallas **no pueden tener una orden de trabajo del vehículo**: no son del
    vehículo. Exigirles una obligaría a abrir órdenes por trabajo que nunca se
    hizo, y no ofrecer nada las dejaría abiertas para siempre.

    La excepción la acota el DATO, no una elección del usuario: sólo la fuente
    `SourceGeotabGoId` entra por aquí.

    Reintroduce el cierre con nota suelta que se retiró el 2026-09-01 cuando
    gestionar pasó a exigir el desenlace. La contradicción es sólo aparente:
    aquello obligaba a declarar qué se hizo cuando había algo que declarar; aquí
    no lo hay, y pedirlo sería pedir que alguien lo invente.
    """

    if not is_telematics_fault(context.signature):
        raise NavifaultConfirmationError(
            "Sólo las fallas del equipo telemático se cierran con una nota. "
            "Para las del vehículo hay que registrar la orden en la que se atendieron."
        )
    limpia = note.strip()
    if not limpia:
        raise NavifaultConfirmationError("La nota es obligatoria: es lo único que queda del cierre")

    case, _accion = await mark_fault_managed(
        session, context=context, actor=actor, note=limpia
    )
    return case

async def revert_unsupported_confirmations(session: AsyncSession) -> dict[str, Any]:
    """Revierte las gestiones cuya orden dejó de sostenerlas.

    Es lo que sustituye a Deshacer. Nadie revierte desde el portal: si hay que
    revertir se revierte en CloudFleet —anulando la orden o quitándole el
    trabajo con la referencia— y el portal lo hereda. Deshacer a mano dejaría el
    proceso del taller huérfano: el portal diciendo una cosa y la orden otra.

    Sólo mira las gestiones dentro de la ventana de verificación. Pasada ésa el
    ciclo cerró y volver a preguntar por su orden sería gastar peticiones en algo
    que ya no cambia nada.

    La reversión queda en la bitácora como una acción `unmanaged` sin autor: la
    hizo el sistema al observar la orden, y atribuírsela a una persona sería
    falsear quién decidió qué.
    """

    limite = datetime.now(UTC) - timedelta(days=MANAGEMENT_TIME_WINDOW_DAYS)
    casos = (
        await session.execute(
            select(NavifaultManagedFaultCase).where(
                NavifaultManagedFaultCase.confirmed_work_order_number.is_not(None),
                NavifaultManagedFaultCase.managed_through_at.is_not(None),
                NavifaultManagedFaultCase.last_managed_at >= limite,
            )
        )
    ).scalars().all()

    resumen: dict[str, Any] = {"checked": 0, "reverted": 0, "reverted_cases": []}
    for caso in casos:
        contexto = await context_for_case(session, case=caso)
        if contexto is None:
            continue
        resumen["checked"] += 1
        try:
            encontrado = await find_fault_work(
                vehicle_id=caso.vehicle_id,
                signature=contexto.signature,
                cycle_number=caso.cycle_number,
                work_order_number=caso.confirmed_work_order_number or 0,
            )
        except NavifaultOrderError:
            # La orden ya no existe: eso también es dejar de sostener la gestión.
            encontrado = None
        if encontrado is not None and encontrado.has_work:
            continue

        await unmark_fault_managed(
            session,
            context=contexto,
            actor=None,
            note=(
                f"La orden {caso.confirmed_work_order_number} dejó de sostener la gestión: "
                "no tiene ningún trabajo con la referencia de esta falla."
            ),
        )
        resumen["reverted"] += 1
        resumen["reverted_cases"].append(str(caso.case_id))
        log.info(
            "navifault_confirmation_reverted",
            case_id=str(caso.case_id),
            work_order_number=caso.confirmed_work_order_number,
        )
    await session.commit()
    return resumen
