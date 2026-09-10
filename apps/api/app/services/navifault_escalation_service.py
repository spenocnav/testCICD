"""Escalar una falla de Navifault a una novedad de CloudFleet.

Escalar **no** es un paso previo a gestionar ni una forma de dar la falla por
atendida: es cambiar el régimen de seguimiento cuando la falla no se puede
resolver ya. El seguimiento estricto del portal se hace en CloudFleet, que es
donde el equipo mira al ir a intervenir el vehículo, y lo que se busca es que la
falla aparezca allí en la próxima intervención.

Por eso este servicio **nunca toca `managed_through_at`**. Marcarla gestionada
arrancaría la verificación de 30 días sobre una reparación que no ocurrió, y
falsearía la efectividad de los desenlaces y el tiempo promedio de gestión.

Vive aparte de `navifault_management_service` a propósito: la lógica de un
proveedor externo no pertenece al servicio que decide estados.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics import FactFaultEvent
from app.models.navifault import NavifaultManagedFaultAction, NavifaultManagedFaultCase
from app.models.novedad import Novedad
from app.models.user import User
from app.services import cloudfleet_service, novedad_service
from app.services.navifault_management_service import (
    WORK_ORDER_FINISHED_STATUSES,
    ManagementFaultContext,
    advance_cycle_if_needed,
    first_occurrence_since,
    has_reappeared_since_management,
    lock_or_create_case,
    management_tracking_started_at,
    navifault_reference,
)

log = logging.getLogger(__name__)


class NavifaultEscalationError(RuntimeError):
    """El escalamiento no procede con el estado actual de la falla."""


class NavifaultAlreadyEscalatedError(NavifaultEscalationError):
    """El ciclo vigente ya tiene una novedad abierta persiguiendo esta falla."""


def _lamps(row: Any) -> str:
    encendidas = [
        nombre
        for nombre, activa in (
            ("parada roja", row["luz_de_parada_roja"]),
            ("parada ámbar", row["luz_de_parada_amber"]),
            ("avería", row["lampara_de_averia"]),
            ("advertencia", row["lampara_de_advertencia"]),
        )
        if activa
    ]
    return ", ".join(encendidas) if encendidas else "ninguna"


def build_escalation_comment(
    *,
    fault_row: Any,
    plate: str,
    user_comment: str | None,
    reference: str | None = None,
) -> str:
    """El texto que lee el taller: la firma resuelta más lo que escriba el usuario.

    Lleva la firma completa porque quien abre la orden en CloudFleet no tiene la
    ficha de Navifault delante: sin el código, el FMI y el controlador no puede
    saber de qué falla se le habla.

    El texto del usuario va PRIMERO. Es lo único que aporta contexto que el
    sistema no conoce, y enterrarlo bajo ocho líneas de datos lo haría invisible.

    **Los campos se separan con ` · ` y no sólo con saltos de línea.** CloudFleet
    copia este texto a la descripción del trabajo eliminando los saltos, y con
    ellos como única separación los campos quedaban pegados —`JTX904Código:`,
    `FMI 9Diagnóstico:`—. Verificado sobre la orden 5733: el comentario llegó al
    trabajo con los 11 saltos suprimidos y ningún carácter en su lugar.
    """

    fmi = fault_row["codigo_modo_de_falla"]
    ultima = fault_row["fecha_de_falla"]
    campos = [
        f"Vehículo: {plate}",
        f"Código: {fault_row['codigo_diagnostico']}" + (f" | FMI {fmi:g}" if fmi is not None else ""),
        f"Diagnóstico: {fault_row['diagnostico'] or 'sin descripción'}",
        f"Modo de falla: {fault_row['modo_de_falla'] or 'sin descripción'}",
        f"Controlador: {fault_row['nombre_de_controlador'] or 'sin dato'}",
        f"Lámparas activas: {_lamps(fault_row)}",
        f"Atención: {fault_row['tipo_de_atencion'] or 'sin clasificar'}",
        f"Última ocurrencia: {ultima:%Y-%m-%d %H:%M}" if ultima else "Última ocurrencia: —",
        f"Ocurrencias reportadas: {fault_row['recuento_de_fallos'] or 1}",
    ]
    if reference:
        campos.append(f"Ref. Navifault: {reference}")

    bloque = "— Escalada desde Navifault — " + " · ".join(campos)

    partes: list[str] = []
    if user_comment and user_comment.strip():
        partes.append(user_comment.strip())
        partes.append("")
    partes.append(bloque)
    return "\n".join(partes)


async def _fault_display_row(session: AsyncSession, fault_row_id: str) -> Any:
    fact = FactFaultEvent
    statement = select(
        fact.codigo_diagnostico,
        fact.codigo_modo_de_falla,
        fact.diagnostico,
        fact.modo_de_falla,
        fact.nombre_de_controlador,
        fact.tipo_de_atencion,
        fact.luz_de_parada_roja,
        fact.luz_de_parada_amber,
        fact.lampara_de_averia,
        fact.lampara_de_advertencia,
        fact.fecha_de_falla,
        fact.recuento_de_fallos,
    ).where(fact.row_id == fault_row_id)
    return (await session.execute(statement)).mappings().one()


async def active_escalation(
    session: AsyncSession, *, case: NavifaultManagedFaultCase
) -> Novedad | None:
    """La novedad que persigue el ciclo vigente, si sigue abierta.

    Deja de bloquear cuando el taller TERMINÓ su orden: la falla siguió
    apareciendo y volver a mandarla es lo que corresponde.

    No basta con `external_is_done`. Comprobado contra el proveedor el
    2026-09-05: asignar la novedad a una orden la marca hecha en el acto,
    con la orden abierta y el vehículo sin intervenir, así que preguntar sólo
    por ese campo permitía escalar de nuevo mientras el taller trabajaba y
    duplicarle la orden. Lo que distingue una cosa de la otra es el estado de
    la ORDEN, y sin estado conocido no se puede afirmar que terminó.

    Una novedad cuya issue **fue borrada en CloudFleet** tampoco bloquea: no hay
    nada persiguiendo la falla y sin esto quedaría escalada para siempre contra
    un número que ya no existe. El enlace se conserva en el caso —la falla sí
    fue escalada y eso es historia— pero deja de gobernar el estado.
    """

    if case.escalated_novedad_id is None or case.managed_through_at is not None:
        return None
    novedad = await session.get(Novedad, case.escalated_novedad_id)
    if novedad is None or novedad.external_deleted_at is not None:
        return None
    if (
        novedad.external_is_done is True
        and novedad.external_work_order_status in WORK_ORDER_FINISHED_STATUSES
    ):
        return None
    return novedad


async def escalate_fault(
    session: AsyncSession,
    *,
    context: ManagementFaultContext,
    actor: User,
    priority: str,
    comment: str | None,
    odometer: Decimal | None,
    send_mail: bool,
    reported_by_id: int,
    idempotency_key: str | None,
) -> tuple[Novedad, NavifaultManagedFaultCase, bool]:
    """Crea la issue, la novedad local y ata el caso al escalamiento.

    Devuelve ``(novedad, caso, creada)``. ``creada`` es False cuando la
    `Idempotency-Key` reencontró una novedad ya existente: ahí no se vuelve a
    llamar al proveedor.

    Orden CloudFleet-primero, heredado del alta de novedades (SEC-007) en vez de
    inventar un segundo patrón para el mismo efecto externo. La consecuencia hay
    que aceptarla: si el commit local falla después de crear la issue, queda una
    issue huérfana **que no se puede borrar** —CloudFleet responde 405 a DELETE—,
    y por eso el número se registra en el log.
    """

    fault_row = await _fault_display_row(session, context.fault_row_id)
    reported_at = fault_row["fecha_de_falla"] or datetime.now(UTC)
    if reported_at.tzinfo is None:
        reported_at = reported_at.replace(tzinfo=UTC)

    case = await lock_or_create_case(session, context=context)
    # Escalar es una acción sobre la falla, y es uno de los dos momentos en que
    # el ciclo puede avanzar. Va con el caso ya bloqueado.
    cycle_number = await advance_cycle_if_needed(session, case=case, context=context)
    # La referencia se deriva de la falla y su ciclo, así que es la misma en
    # cada intento y puede entrar en el texto sin romper la idempotencia: un
    # reintento con la misma Idempotency-Key produce el mismo comentario y el
    # mismo fingerprint.
    reference = navifault_reference(
        vehicle_id=context.vehicle.id, signature=context.signature, cycle_number=cycle_number
    )
    cloudfleet_comment = build_escalation_comment(
        fault_row=fault_row,
        plate=context.vehicle.plate,
        user_comment=comment,
        reference=reference,
    )
    abierta = await active_escalation(session, case=case)
    if abierta is not None:
        raise NavifaultAlreadyEscalatedError(
            f"La falla ya está escalada en la novedad #{abierta.cloudfleet_issue_number}. "
            "Ciérrala en CloudFleet antes de volver a escalarla."
        )
    # Una falla GESTIONADA que no ha vuelto a aparecer no se escala. Escalar es
    # cambiar el régimen de seguimiento de algo que sigue pasando; si la falla
    # se atendió y se calló, no hay nada que perseguir y la novedad nueva sólo
    # le duplicaría el trabajo al taller —irreversible, porque CloudFleet
    # responde 405 a DELETE—.
    #
    # Se abre sola en cuanto la falla vuelve: dentro de los 30 días el listado
    # la llama `repeated` y después abre ciclo nuevo como `pending`. Las dos son
    # escalables, así que esta guarda bloquea exactamente el estado `managed`.
    #
    # Y si la gestión estuvo mal, la salida no es contradecirla desde el portal
    # —para eso se retiró Deshacer— sino corregir la orden en CloudFleet: el
    # vigilante hereda la reversión y la falla vuelve a ser escalable.
    if case.managed_through_at is not None and not await has_reappeared_since_management(
        session, context=context, case=case
    ):
        raise NavifaultEscalationError(
            "La falla está gestionada y no ha vuelto a aparecer, así que no hay nada que "
            "escalar. Si vuelve a presentarse podrás escalarla de nuevo; si la gestión no "
            "corresponde, corrige la orden en CloudFleet y el portal lo hereda."
        )

    fingerprint = novedad_service.compute_request_fingerprint(
        vehicle_id=context.vehicle.id,
        reported_at=reported_at,
        priority=priority,
        odometer=odometer,
        comment=cloudfleet_comment,
        send_mail=send_mail,
    )
    if idempotency_key:
        existente = await novedad_service.find_by_idempotency_key(
            session, created_by_id=actor.id, idempotency_key=idempotency_key
        )
        if existente is not None:
            if existente.request_fingerprint != fingerprint:
                raise NavifaultEscalationError(
                    "Idempotency-Key reutilizada con un payload distinto"
                )
            _attach_escalation(case, novedad=existente, actor=actor)
            await session.flush()
            return existente, case, False

    payload = cloudfleet_service.build_issue_payload(
        vehicle_code=context.vehicle.plate,
        reported_at=reported_at.isoformat(),
        reported_by_id=reported_by_id,
        priority=priority,
        odometer=odometer,
        comment=cloudfleet_comment,
        send_mail=send_mail,
    )
    resultado = await cloudfleet_service.create_issue(payload)

    try:
        novedad = await novedad_service.create_local_novedad(
            session,
            vehicle_id=context.vehicle.id,
            fleet_id=context.vehicle.fleet_id,
            created_by=actor,
            vehicle_code=context.vehicle.plate,
            reported_at=reported_at,
            reported_by_id=reported_by_id,
            priority=priority,
            odometer=odometer,
            comment=cloudfleet_comment,
            send_mail=send_mail,
            idempotency_key=idempotency_key or None,
            request_fingerprint=fingerprint,
            navifault_reference=reference,
        )
        novedad_service.mark_sent(
            novedad, issue_number=resultado.issue_number, response=resultado.response
        )
        _attach_escalation(case, novedad=novedad, actor=actor)
        session.add(
            NavifaultManagedFaultAction(
                case_id=case.case_id,
                action_type="escalated",
                managed_at=case.escalated_at,
                managed_through_at=None,
                managed_through_row_id=context.fault_row_id,
                actor_user_id=actor.id,
                note=f"Escalada a la novedad #{resultado.issue_number}",
                cycle_started_at=await _cycle_started_at_for_escalation(session, context=context),
            )
        )
        await session.flush()
    except Exception:
        log.warning(
            "La issue %s de CloudFleet quedó creada pero el escalamiento falló localmente; "
            "hay que cerrarla a mano en CloudFleet.",
            resultado.issue_number,
        )
        raise

    return novedad, case, True


def _attach_escalation(
    case: NavifaultManagedFaultCase, *, novedad: Novedad, actor: User
) -> None:
    """Ata el escalamiento al caso SIN tocar el checkpoint de gestión."""

    case.escalated_novedad_id = novedad.id
    case.escalated_at = datetime.now(UTC)
    case.escalated_by_user_id = actor.id
    case.updated_at = datetime.now(UTC)


async def _cycle_started_at_for_escalation(
    session: AsyncSession, *, context: ManagementFaultContext
) -> datetime | None:
    """Primera ocurrencia del ciclo que se está escalando.

    Es lo que permite medir cuánto tardó la falla en llegar al taller: de la
    aparición al escalamiento, igual que el tiempo de gestión se mide de la
    aparición al cierre.

    Se guarda en la ACCIÓN y no en `current_cycle_started_at` del caso a
    propósito. Esa columna la calcula y la escribe el flujo de gestión con sus
    propias reglas de reversión y reaparición; escribirla desde aquí cambiaría
    la rama por la que ese flujo pasa —el caso dejaría de ser "recién creado"—
    y con ella el tiempo de gestión que ya se publica. La bitácora es
    append-only y cada acción guarda el ciclo que le corresponde.

    `None` si la firma no tiene ninguna ocurrencia dentro de la ventana de
    seguimiento: sin punto de partida no se inventa uno.
    """

    tracking_started_at = await management_tracking_started_at(session)
    ultima_gestion = None
    case = await session.scalar(
        select(NavifaultManagedFaultCase.last_managed_at).where(
            NavifaultManagedFaultCase.vehicle_id == context.vehicle.id,
            NavifaultManagedFaultCase.signature_sha256 == context.signature.sha256,
        )
    )
    if case is not None:
        # Tras una gestión previa, el ciclo empieza con la reaparición, no con
        # la primera ocurrencia histórica.
        ultima_gestion = case
    return await first_occurrence_since(
        session,
        vehicle_id=context.vehicle.id,
        signature=context.signature,
        tracking_started_at=tracking_started_at,
        after=ultima_gestion,
    )


