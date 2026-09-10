"""Bandeja interna de gestión de fallas Navifault.

Los hechos de Geotab se conservan en ``analytics.fact_fault_event`` y no se
mutan. Este servicio agrupa cada firma operacional por vehículo del Portal y la
compara con una marca de agua privada: si aparece un evento posterior a la
gestión, la misma firma reaparece con estado ``repeated``.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import and_, case, func, literal, or_, select, true
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.logging import get_logger
from app.models.analytics import DimVehicle, FactFaultEvent
from app.models.cloudfleet import CloudfleetVehicle
from app.models.master_data import GeotabDatabase, Vehicle
from app.models.navifault import (
    NavifaultManagedFaultAction,
    NavifaultManagedFaultCase,
    NavifaultManagementConfiguration,
)
from app.models.novedad import Novedad
from app.models.user import User

log = get_logger("navifault-management")

FaultManagementState = Literal[
    "pending",
    "escalada",
    "pendiente_registro",
    "repeated",
    "managed",
]
MANAGEMENT_TIME_WINDOW_DAYS = 30
REPEAT_WINDOW_DAYS = 30
REPEAT_WINDOW = timedelta(days=REPEAT_WINDOW_DAYS)


@dataclass(frozen=True)
class ManagedFaultSignature:
    """Firma canónica ya acordada para distinguir una falla Geotab.

    Los textos de diagnóstico/controlador no forman parte de la identidad; esos
    textos pueden variar sin que la falla técnica cambie. El `source` sí es
    esencial porque define el espacio de numeración del código.
    """

    source: str
    diagnostic_code: int | None
    failure_mode: float | None
    stop_amber: bool | None
    stop_red: bool | None
    malfunction: bool | None
    warning: bool | None

    @property
    def sha256(self) -> str:
        payload = {
            "source": self.source,
            "diagnostic_code": self.diagnostic_code,
            "failure_mode": _canonical_number(self.failure_mode),
            "stop_amber": self.stop_amber,
            "stop_red": self.stop_red,
            "malfunction": self.malfunction,
            "warning": self.warning,
        }
        canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


#: Alfabeto sin caracteres que se confundan al transcribir: fuera 0/O y 1/I/L.
_REFERENCE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
_REFERENCE_LENGTH = 8


def navifault_reference(
    *, vehicle_id: uuid.UUID, signature: ManagedFaultSignature, cycle_number: int = 1
) -> str:
    """Identificador de la falla que el taller copia en los trabajos de la orden.

    Es la única forma de saber qué trabajos corresponden a qué falla: CloudFleet
    no publica ese vínculo por API en ninguna de las dos direcciones, y sólo
    admite un trabajo por novedad. Copiarlo es un acto deliberado de una
    persona, así que un trabajo que lo lleva es una declaración, no una
    coincidencia de texto.

    **Se deriva, no se guarda.** Sale del vehículo, la firma y el ciclo, así que
    existe desde que la falla aparece —sin haberla escalado y sin fila en
    ninguna tabla— y se recalcula igual. No hace falta el camino inverso, de
    referencia a falla: el flujo siempre parte de una falla concreta y busca SU
    referencia dentro de una orden.

    **El ciclo entra en la identidad** porque sin él la referencia era la misma
    durante toda la vida de la falla, y entonces una orden vieja llevaba la
    misma marca que una nueva: un ciclo podía cerrarse con trabajo de otro. Con
    el ciclo dentro, un trabajo marcado sólo puede pertenecer al ciclo que lo
    marcó, y sobran las reglas de fecha que harían falta si no.

    Una repetición dentro de los 30 días conserva el número: es el mismo ciclo,
    el que la gestión no logró cerrar.
    """

    material = f"{vehicle_id}:{signature.sha256}:{cycle_number}".encode()
    digest = hashlib.sha256(material).digest()
    valor = int.from_bytes(digest[:8], "big")
    base = len(_REFERENCE_ALPHABET)
    cuerpo = []
    for _ in range(_REFERENCE_LENGTH):
        valor, resto = divmod(valor, base)
        cuerpo.append(_REFERENCE_ALPHABET[resto])
    return "NF-" + "".join(reversed(cuerpo))


@dataclass(frozen=True)
class ManagementFaultContext:
    vehicle: Vehicle
    fault_row_id: str
    signature: ManagedFaultSignature


#: Fuente de las fallas que reporta el propio equipo telemático sobre sí mismo:
#: reinicios internos, pérdida de energía, desconexión, cámara ausente. Son el
#: 15,9 % de los eventos del hecho. La llave es la FUENTE y no el nombre del
#: controlador: `Telematics device` es un nombre para mostrar y puede cambiar o
#: traducirse, mientras que las dos columnas señalan exactamente el mismo
#: conjunto —165.030 filas cada una, cero discrepancias sobre 1.041.014—.
TELEMATICS_SOURCE = "SourceGeotabGoId"


def is_telematics_fault(signature: ManagedFaultSignature) -> bool:
    """¿La falla la reporta el equipo telemático sobre sí mismo?

    Vive junto a la firma, que es de quien es la pregunta, y no en el servicio
    de órdenes: la consultan el cierre con nota Y el contrato que decide qué
    ofrece la pantalla, y con la regla en dos sitios una falla podría cerrarse
    con nota sin que la interfaz lo ofreciera, o al revés.
    """

    return signature.source.strip() == TELEMATICS_SOURCE


def _canonical_number(value: float | None) -> str | None:
    if value is None:
        return None
    return format(float(value), ".15g")


def _normalized_diagnostic_expression(fault: Any) -> Any:
    return func.lower(func.regexp_replace(func.coalesce(fault.diagnostico, ""), "^[[:space:]*]+", ""))


def _normalized_source_expression(fault: Any) -> Any:
    """Normaliza la fuente igual que la firma escrita en el caso privado."""

    return func.btrim(func.coalesce(fault.nombre_fuente_diagnostico, ""))


def _signature_join_conditions(fault: Any, managed_case: Any, vehicle: Any) -> list[Any]:
    """Condiciones SQL equivalentes a :class:`ManagedFaultSignature`.

    Se usa `IS NOT DISTINCT FROM` para no confundir una lámpara o FMI ausente
    con su valor explícito. El hash resuelve unicidad al escribir; estas
    condiciones permiten confrontar los hechos con la gestión de forma
    set-based al leer la bandeja.
    """

    return [
        managed_case.vehicle_id == vehicle.id,
        managed_case.source == _normalized_source_expression(fault),
        managed_case.diagnostic_code.is_not_distinct_from(fault.codigo_diagnostico),
        managed_case.failure_mode.is_not_distinct_from(fault.codigo_modo_de_falla),
        managed_case.stop_amber.is_not_distinct_from(fault.luz_de_parada_amber),
        managed_case.stop_red.is_not_distinct_from(fault.luz_de_parada_roja),
        managed_case.malfunction.is_not_distinct_from(fault.lampara_de_averia),
        managed_case.warning.is_not_distinct_from(fault.lampara_de_advertencia),
    ]


def _first_reappearance_expression(fault: Any, managed_case: Any) -> Any:
    """Primera ocurrencia posterior al checkpoint, aun fuera de la página UI.

    La tabla visible agrupa y limita filas. Esta subconsulta correlacionada usa
    el histórico completo de la misma firma y vehículo para que una reaparición
    temprana siga siendo repetida aunque luego haya más ocurrencias.
    """

    reappearance = aliased(FactFaultEvent)
    return (
        select(func.min(reappearance.fecha_de_falla))
        .where(
            reappearance.vehicle_id == fault.vehicle_id,
            _normalized_source_expression(reappearance) == managed_case.source,
            reappearance.codigo_diagnostico.is_not_distinct_from(
                managed_case.diagnostic_code
            ),
            reappearance.codigo_modo_de_falla.is_not_distinct_from(managed_case.failure_mode),
            reappearance.luz_de_parada_amber.is_not_distinct_from(managed_case.stop_amber),
            reappearance.luz_de_parada_roja.is_not_distinct_from(managed_case.stop_red),
            reappearance.lampara_de_averia.is_not_distinct_from(managed_case.malfunction),
            reappearance.lampara_de_advertencia.is_not_distinct_from(managed_case.warning),
            reappearance.fecha_de_falla > managed_case.managed_through_at,
            reappearance.fecha_de_falla > managed_case.last_managed_at,
            _normalized_diagnostic_expression(reappearance).not_like("unknown%"),
        )
        .correlate(fault, managed_case)
        .scalar_subquery()
    )


async def fault_evidence(
    session: AsyncSession,
    *,
    context: ManagementFaultContext,
) -> list[dict[str, Any]]:
    """Qué se hizo con esta MISMA falla en otros vehículos, sin decir en cuáles.

    Cruza todas las flotas y por eso es **anónima**: ni placa, ni vehículo, ni
    flota, ni autor. El conocimiento técnico sobre un código es de Navitrans; el
    dato operativo es del cliente. Acotarla al alcance del usuario dejaría con
    la lista vacía justo al cliente pequeño, que es quien más la necesita. El
    alcance de flota sí gobierna el acceso a la FALLA: sin él no se llega aquí.

    La identidad es la **firma completa** —fuente, código, FMI y las cuatro
    lámparas—, no el código suelto. Agregar evidencia de otra variante es el
    mismo error que el módulo evita al no resolver fallas ambiguas por intuición.

    La verificación se publica como CONTEO y nunca como porcentaje: con dos
    casos, un "100 % efectivo" es una mentira vestida de estadística. Y un
    cierre reciente no cuenta ni a favor ni en contra —queda en `pending`—
    porque la ventana de 30 días todavía no ha transcurrido; afirmar que
    funcionó antes de que pase es justo lo que esa ventana existe para impedir.
    """

    caso = aliased(NavifaultManagedFaultCase)
    vehiculo = aliased(Vehicle)
    accion = aliased(NavifaultManagedFaultAction)
    fact = aliased(FactFaultEvent)
    dim = aliased(DimVehicle)
    base = aliased(GeotabDatabase)

    firma = context.signature
    # La falla volvió a aparecer en ESE vehículo después de su gestión. El
    # puente del caso al hecho es el mismo de la bandeja: portal -> base Geotab
    # -> dimensión -> hecho.
    reaparecio = (
        select(fact.row_id)
        .join(dim, dim.vehicle_id == fact.vehicle_id)
        .join(base, func.lower(base.database_key) == func.lower(dim.database_name))
        .where(
            base.id == vehiculo.geotab_database_id,
            dim.device_id == vehiculo.geotab_device_id,
            fact.movil == vehiculo.plate,
            _normalized_source_expression(fact) == caso.source,
            fact.codigo_diagnostico.is_not_distinct_from(caso.diagnostic_code),
            fact.codigo_modo_de_falla.is_not_distinct_from(caso.failure_mode),
            fact.luz_de_parada_amber.is_not_distinct_from(caso.stop_amber),
            fact.luz_de_parada_roja.is_not_distinct_from(caso.stop_red),
            fact.lampara_de_averia.is_not_distinct_from(caso.malfunction),
            fact.lampara_de_advertencia.is_not_distinct_from(caso.warning),
            fact.fecha_de_falla > caso.managed_through_at,
            fact.fecha_de_falla > caso.last_managed_at,
        )
        .correlate(caso, vehiculo)
        .exists()
    )
    ventana_cumplida = caso.last_managed_at + REPEAT_WINDOW <= func.now()

    statement = (
        select(
            accion.note,
            accion.details,
            accion.work_order_number,
            caso.last_managed_at,
            reaparecio.label("volvio"),
            ventana_cumplida.label("ventana_cumplida"),
        )
        .select_from(caso)
        .join(vehiculo, vehiculo.id == caso.vehicle_id)
        .join(
            accion,
            accion.action_id
            == (
                select(NavifaultManagedFaultAction.action_id)
                .where(
                    NavifaultManagedFaultAction.case_id == caso.case_id,
                    NavifaultManagedFaultAction.action_type == "managed",
                )
                .order_by(NavifaultManagedFaultAction.managed_at.desc())
                .limit(1)
                .correlate(caso)
                .scalar_subquery()
            ),
        )
        .where(
            caso.managed_through_at.is_not(None),
            caso.last_managed_at.is_not(None),
            # El caso de ESTE vehículo no es evidencia de sí mismo.
            caso.vehicle_id != context.vehicle.id,
            caso.source == firma.source,
            caso.diagnostic_code.is_not_distinct_from(firma.diagnostic_code),
            caso.failure_mode.is_not_distinct_from(firma.failure_mode),
            caso.stop_amber.is_not_distinct_from(firma.stop_amber),
            caso.stop_red.is_not_distinct_from(firma.stop_red),
            caso.malfunction.is_not_distinct_from(firma.malfunction),
            caso.warning.is_not_distinct_from(firma.warning),
        )
        .order_by(caso.last_managed_at.desc())
    )
    filas = (await session.execute(statement)).mappings().all()
    return [
        {
            "note": fila["note"],
            "details": fila["details"],
            "work_order_number": fila["work_order_number"],
            "managed_at": fila["last_managed_at"],
            # Tres estados y no dos: "todavía no se sabe" es una respuesta.
            "verification": (
                "returned"
                if fila["volvio"]
                else "held"
                if fila["ventana_cumplida"]
                else "pending"
            ),
        }
        for fila in filas
    ]


async def has_reappeared_since_management(
    session: AsyncSession,
    *,
    context: ManagementFaultContext,
    case: NavifaultManagedFaultCase,
) -> bool:
    """¿La firma volvió a aparecer después de que alguien la gestionara?

    Es la misma pregunta que :func:`_first_reappearance_expression` resuelve
    dentro de los listados, aquí para un caso suelto. La comparten porque de
    ella depende el estado que se pinta Y quién puede volver a escalar, y nada
    más obligaría a las dos a coincidir.

    `False` con gestión hecha es exactamente el estado ``managed``: la falla se
    atendió y no ha vuelto. Si hubiera vuelto, el listado la llamaría
    ``repeated`` dentro de los 30 días y ``pending`` —ciclo nuevo— después.
    """

    if case.managed_through_at is None or case.last_managed_at is None:
        return False
    fact = FactFaultEvent
    # El caso guarda el vehículo del PORTAL; el hecho razona por el de
    # analytics. La fila que se está gestionando es el puente entre los dos.
    vehiculo_analytics = (
        select(fact.vehicle_id).where(fact.row_id == context.fault_row_id).scalar_subquery()
    )
    statement = select(
        select(fact.row_id)
        .where(
            fact.vehicle_id == vehiculo_analytics,
            _normalized_source_expression(fact) == case.source,
            fact.codigo_diagnostico.is_not_distinct_from(case.diagnostic_code),
            fact.codigo_modo_de_falla.is_not_distinct_from(case.failure_mode),
            fact.luz_de_parada_amber.is_not_distinct_from(case.stop_amber),
            fact.luz_de_parada_roja.is_not_distinct_from(case.stop_red),
            fact.lampara_de_averia.is_not_distinct_from(case.malfunction),
            fact.lampara_de_advertencia.is_not_distinct_from(case.warning),
            fact.fecha_de_falla > case.managed_through_at,
            fact.fecha_de_falla > case.last_managed_at,
            _normalized_diagnostic_expression(fact).not_like("unknown%"),
        )
        .exists()
    )
    return bool((await session.execute(statement)).scalar_one())


async def _management_context(
    session: AsyncSession, fault_row_id: str
) -> ManagementFaultContext | None:
    fact = FactFaultEvent
    statement = (
        select(
            Vehicle,
            fact.nombre_fuente_diagnostico,
            fact.codigo_diagnostico,
            fact.codigo_modo_de_falla,
            fact.luz_de_parada_amber,
            fact.luz_de_parada_roja,
            fact.lampara_de_averia,
            fact.lampara_de_advertencia,
        )
        .select_from(fact)
        .join(DimVehicle, DimVehicle.vehicle_id == fact.vehicle_id)
        .join(
            GeotabDatabase,
            func.lower(GeotabDatabase.database_key) == func.lower(DimVehicle.database_name),
        )
        .join(
            Vehicle,
            and_(
                Vehicle.geotab_database_id == GeotabDatabase.id,
                Vehicle.geotab_device_id == DimVehicle.device_id,
                Vehicle.plate == fact.movil,
                Vehicle.is_active.is_(True),
            ),
        )
        .where(
            fact.row_id == fault_row_id,
            _normalized_diagnostic_expression(fact).not_like("unknown%"),
        )
    )
    rows = (await session.execute(statement)).all()
    if len(rows) != 1:
        return None
    row = rows[0]
    return ManagementFaultContext(
        vehicle=row.Vehicle,
        fault_row_id=fault_row_id,
        signature=ManagedFaultSignature(
            source=str(row.nombre_fuente_diagnostico or "").strip(),
            diagnostic_code=row.codigo_diagnostico,
            failure_mode=row.codigo_modo_de_falla,
            stop_amber=row.luz_de_parada_amber,
            stop_red=row.luz_de_parada_roja,
            malfunction=row.lampara_de_averia,
            warning=row.lampara_de_advertencia,
        ),
    )


async def get_management_context(
    session: AsyncSession, fault_row_id: str
) -> ManagementFaultContext | None:
    """Obtiene la identidad Portal + firma de una fila visible de Geotab."""

    return await _management_context(session, fault_row_id)


def _matching_occurrences_statement(
    *, vehicle_id: uuid.UUID, signature: ManagedFaultSignature
) -> Any:
    fact = FactFaultEvent
    return (
        select(fact.fecha_de_falla, fact.row_id)
        .select_from(fact)
        .join(DimVehicle, DimVehicle.vehicle_id == fact.vehicle_id)
        .join(
            GeotabDatabase,
            func.lower(GeotabDatabase.database_key) == func.lower(DimVehicle.database_name),
        )
        .join(
            Vehicle,
            and_(
                Vehicle.geotab_database_id == GeotabDatabase.id,
                Vehicle.geotab_device_id == DimVehicle.device_id,
                Vehicle.plate == fact.movil,
                Vehicle.is_active.is_(True),
            ),
        )
        .where(
            Vehicle.id == vehicle_id,
            _normalized_source_expression(fact) == signature.source,
            fact.codigo_diagnostico.is_not_distinct_from(signature.diagnostic_code),
            fact.codigo_modo_de_falla.is_not_distinct_from(signature.failure_mode),
            fact.luz_de_parada_amber.is_not_distinct_from(signature.stop_amber),
            fact.luz_de_parada_roja.is_not_distinct_from(signature.stop_red),
            fact.lampara_de_averia.is_not_distinct_from(signature.malfunction),
            fact.lampara_de_advertencia.is_not_distinct_from(signature.warning),
            _normalized_diagnostic_expression(fact).not_like("unknown%"),
        )
    )


async def _latest_occurrence(
    session: AsyncSession, *, vehicle_id: uuid.UUID, signature: ManagedFaultSignature
) -> tuple[datetime | None, str | None]:
    fact = FactFaultEvent
    statement = _matching_occurrences_statement(vehicle_id=vehicle_id, signature=signature)
    statement = statement.order_by(fact.fecha_de_falla.desc().nullslast(), fact.row_id.desc()).limit(1)
    row = (await session.execute(statement)).first()
    if row is None:
        return None, None
    return row[0], row[1]


async def first_occurrence_since(
    session: AsyncSession,
    *,
    vehicle_id: uuid.UUID,
    signature: ManagedFaultSignature,
    tracking_started_at: datetime,
    after: datetime | None = None,
) -> datetime | None:
    """Primer hecho de la firma dentro del ciclo operativo actual.

    La base global evita que la historia importada antes de lanzar la gestión
    infle artificialmente el tiempo de respuesta. En una repetición, `after`
    es el checkpoint de la gestión anterior y el nuevo ciclo comienza sólo con
    una ocurrencia posterior a dicho punto.
    """

    fact = FactFaultEvent
    statement = _matching_occurrences_statement(vehicle_id=vehicle_id, signature=signature).where(
        fact.fecha_de_falla.is_not(None), fact.fecha_de_falla >= tracking_started_at
    )
    if after is not None:
        statement = statement.where(fact.fecha_de_falla > after)
    row = (
        await session.execute(
            statement.order_by(fact.fecha_de_falla.asc(), fact.row_id.asc()).limit(1)
        )
    ).first()
    return row[0] if row is not None else None


async def management_tracking_started_at(session: AsyncSession) -> datetime:
    started_at = await session.scalar(
        select(NavifaultManagementConfiguration.tracking_started_at).where(
            NavifaultManagementConfiguration.singleton.is_(True)
        )
    )
    if started_at is None:  # pragma: no cover - la migración crea el singleton
        raise RuntimeError("Falta la configuración de inicio de gestión Navifault")
    return started_at


async def _last_reversal(
    session: AsyncSession, *, case_id: uuid.UUID
) -> NavifaultManagedFaultAction | None:
    return await session.scalar(
        select(NavifaultManagedFaultAction)
        .where(
            NavifaultManagedFaultAction.case_id == case_id,
            NavifaultManagedFaultAction.action_type == "unmanaged",
        )
        .order_by(
            NavifaultManagedFaultAction.managed_at.desc(),
            NavifaultManagedFaultAction.action_id.desc(),
        )
        .limit(1)
    )


async def _cycle_started_at_for_management(
    session: AsyncSession,
    *,
    managed_case: NavifaultManagedFaultCase,
    created_case: bool,
    context: ManagementFaultContext,
    latest_occurrence_row_id: str,
    tracking_started_at: datetime,
) -> datetime:
    """Calcula el inicio del ciclo que el gestor está cerrando.

    Una reversión no abre un segundo ciclo: si se vuelve a gestionar el mismo
    checkpoint se recupera el inicio original. Si hubo una ocurrencia nueva,
    el reloj empieza en la primera aparición posterior al checkpoint revocado.
    """

    if created_case:
        return (
            await first_occurrence_since(
                session,
                vehicle_id=context.vehicle.id,
                signature=context.signature,
                tracking_started_at=tracking_started_at,
            )
            or tracking_started_at
        )

    if managed_case.managed_through_at is not None:
        return (
            await first_occurrence_since(
                session,
                vehicle_id=context.vehicle.id,
                signature=context.signature,
                tracking_started_at=tracking_started_at,
                after=managed_case.last_managed_at,
            )
            or tracking_started_at
        )

    reversal = await _last_reversal(session, case_id=managed_case.case_id)
    if reversal is not None and reversal.managed_through_row_id == latest_occurrence_row_id:
        return reversal.cycle_started_at or tracking_started_at
    if reversal is not None:
        return (
            await first_occurrence_since(
                session,
                vehicle_id=context.vehicle.id,
                signature=context.signature,
                tracking_started_at=tracking_started_at,
                after=managed_case.last_managed_at,
            )
            or tracking_started_at
        )
    return (
        await first_occurrence_since(
            session,
            vehicle_id=context.vehicle.id,
            signature=context.signature,
            tracking_started_at=tracking_started_at,
        )
        or tracking_started_at
    )


async def advance_cycle_if_needed(
    session: AsyncSession, *, case: NavifaultManagedFaultCase, context: ManagementFaultContext
) -> int:
    """Avanza el ciclo del caso si la falla ya está en uno nuevo, y lo devuelve.

    Se llama cuando alguien ACTÚA sobre la falla —escalar, registrar una orden—,
    no cuando el ciclo nuevo nace. Un ciclo nuevo lo abre el paso del tiempo y
    nadie escribe una fila en ese instante; mover la referencia ahí dejaría
    obsoleta la que alguien copió días antes. Actuando como disparo, nadie puede
    tener pegada la referencia de un ciclo en el que no ha trabajado nadie.

    La condición es la del estado `pending` posterior a una gestión: hubo
    checkpoint y la primera reaparición llegó **fuera** de la ventana. Una
    repetición dentro de la ventana no avanza nada: es el mismo ciclo, el que la
    gestión no logró cerrar, y el trabajo nuevo es el segundo intento sobre lo
    mismo.
    """

    if case.managed_through_at is None or case.last_managed_at is None:
        return case.cycle_number

    primera_reaparicion = await first_occurrence_since(
        session,
        vehicle_id=context.vehicle.id,
        signature=context.signature,
        tracking_started_at=case.managed_through_at,
        after=case.last_managed_at,
    )
    if primera_reaparicion is None:
        return case.cycle_number
    if primera_reaparicion <= case.last_managed_at + REPEAT_WINDOW:
        return case.cycle_number

    case.cycle_number += 1
    case.updated_at = datetime.now(UTC)
    log.info(
        "navifault_cycle_advanced",
        case_id=str(case.case_id),
        cycle_number=case.cycle_number,
        first_reappearance_at=primera_reaparicion.isoformat(),
    )
    return case.cycle_number


async def latest_action_for_fault(
    session: AsyncSession, *, context: ManagementFaultContext
) -> NavifaultManagedFaultAction | None:
    """Última acción de la bitácora de esta falla, para devolverla al cliente."""

    return await session.scalar(
        select(NavifaultManagedFaultAction)
        .join(
            NavifaultManagedFaultCase,
            NavifaultManagedFaultCase.case_id == NavifaultManagedFaultAction.case_id,
        )
        .where(
            NavifaultManagedFaultCase.vehicle_id == context.vehicle.id,
            NavifaultManagedFaultCase.signature_sha256 == context.signature.sha256,
        )
        .order_by(
            NavifaultManagedFaultAction.managed_at.desc(),
            NavifaultManagedFaultAction.action_id.desc(),
        )
        .limit(1)
    )


async def context_for_case(
    session: AsyncSession, *, case: NavifaultManagedFaultCase
) -> ManagementFaultContext | None:
    """Reconstruye el contexto de una falla a partir de su caso.

    Lo necesita el camino inverso al habitual: el vigilante parte del caso —lo
    que el portal cree— y no de una fila del hecho. `None` si el vehículo ya no
    está activo: sin él no hay falla que gestionar.
    """

    vehicle = await session.get(Vehicle, case.vehicle_id)
    if vehicle is None or not vehicle.is_active:
        return None
    return ManagementFaultContext(
        vehicle=vehicle,
        fault_row_id=case.managed_through_row_id or "",
        signature=ManagedFaultSignature(
            source=case.source,
            diagnostic_code=case.diagnostic_code,
            failure_mode=case.failure_mode,
            stop_amber=case.stop_amber,
            stop_red=case.stop_red,
            malfunction=case.malfunction,
            warning=case.warning,
        ),
    )


async def lock_or_create_case(
    session: AsyncSession, *, context: ManagementFaultContext
) -> NavifaultManagedFaultCase:
    """Toma el caso de la firma, creándolo si no existe, y lo bloquea.

    Un caso creado aquí nace **sin gestión**: `managed_through_at`,
    `last_managed_at` y `last_managed_by_user_id` en NULL. Es exactamente lo que
    la migración `e8f9a0b10057` hizo posible, y lo que impide que un
    escalamiento se cuente como una gestión.
    """

    signature = context.signature
    now = datetime.now(UTC)
    await session.execute(
        insert(NavifaultManagedFaultCase)
        .values(
            vehicle_id=context.vehicle.id,
            signature_sha256=signature.sha256,
            source=signature.source,
            diagnostic_code=signature.diagnostic_code,
            failure_mode=signature.failure_mode,
            stop_amber=signature.stop_amber,
            stop_red=signature.stop_red,
            malfunction=signature.malfunction,
            warning=signature.warning,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=["vehicle_id", "signature_sha256"])
    )
    case = await session.scalar(
        select(NavifaultManagedFaultCase)
        .where(
            NavifaultManagedFaultCase.vehicle_id == context.vehicle.id,
            NavifaultManagedFaultCase.signature_sha256 == signature.sha256,
        )
        .with_for_update()
    )
    if case is None:  # pragma: no cover - protegido por unique + lectura bloqueada
        raise RuntimeError("No fue posible crear el caso de la falla")
    return case


async def mark_fault_managed(
    session: AsyncSession,
    *,
    context: ManagementFaultContext,
    actor: User,
    note: str | None,
    work_order_number: int | None = None,
    details: dict[str, Any] | None = None,
) -> tuple[NavifaultManagedFaultCase, NavifaultManagedFaultAction | None]:
    """Registra una gestión o devuelve la existente si el checkpoint no cambió.

    El checkpoint siempre se adelanta al último evento de esa firma que ya
    existe al momento de gestionar. Por ello la fila desaparece de la bandeja
    activa y sólo una ocurrencia posterior puede volverla `repeated`.

    `work_order_number` es la orden que SOSTIENE la gestión, y queda en el caso
    porque es lo que se vuelve a mirar para saber si sigue en pie: si esa orden
    se anula o le quitan el trabajo, la falla vuelve sola a sin gestionar.
    `details` es la copia de lo confirmado y va a la bitácora, que es
    append-only, de modo que cada ciclo conserva la suya.
    """

    latest_occurrence_at, latest_occurrence_row_id = await _latest_occurrence(
        session, vehicle_id=context.vehicle.id, signature=context.signature
    )
    checkpoint_row_id = latest_occurrence_row_id or context.fault_row_id
    tracking_started_at = await management_tracking_started_at(session)
    initial_cycle_started_at = await first_occurrence_since(
        session,
        vehicle_id=context.vehicle.id,
        signature=context.signature,
        tracking_started_at=tracking_started_at,
    )
    signature_hash = context.signature.sha256
    now = datetime.now(UTC)
    normalized_note = note.strip() if note and note.strip() else None

    case_insert = (
        insert(NavifaultManagedFaultCase)
        .values(
            vehicle_id=context.vehicle.id,
            signature_sha256=signature_hash,
            source=context.signature.source,
            diagnostic_code=context.signature.diagnostic_code,
            failure_mode=context.signature.failure_mode,
            stop_amber=context.signature.stop_amber,
            stop_red=context.signature.stop_red,
            malfunction=context.signature.malfunction,
            warning=context.signature.warning,
            managed_through_at=latest_occurrence_at,
            managed_through_row_id=checkpoint_row_id,
            current_cycle_started_at=initial_cycle_started_at or tracking_started_at,
            last_managed_at=now,
            last_managed_by_user_id=actor.id,
            last_note=normalized_note,
            confirmed_work_order_number=work_order_number,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=["vehicle_id", "signature_sha256"])
        .returning(NavifaultManagedFaultCase.case_id)
    )
    insert_result = await session.execute(case_insert)
    created_case = insert_result.scalar_one_or_none() is not None
    managed_case = await session.scalar(
        select(NavifaultManagedFaultCase)
        .where(
            NavifaultManagedFaultCase.vehicle_id == context.vehicle.id,
            NavifaultManagedFaultCase.signature_sha256 == signature_hash,
        )
        .with_for_update()
    )
    if managed_case is None:  # pragma: no cover - protegido por unique + lectura bloqueada
        raise RuntimeError("No fue posible crear el caso de gestión Navifault")

    already_managed_through_latest = (
        latest_occurrence_at is not None
        and managed_case.managed_through_at is not None
        and managed_case.managed_through_at >= latest_occurrence_at
    )
    already_managed_without_timestamp = (
        latest_occurrence_at is None
        and managed_case.managed_through_row_id == checkpoint_row_id
    )
    if (not created_case) and (
        already_managed_through_latest or already_managed_without_timestamp
    ):
        return managed_case, None

    cycle_started_at = await _cycle_started_at_for_management(
        session,
        managed_case=managed_case,
        created_case=created_case,
        context=context,
        latest_occurrence_row_id=checkpoint_row_id,
        tracking_started_at=tracking_started_at,
    )
    managed_case.managed_through_at = latest_occurrence_at
    managed_case.managed_through_row_id = checkpoint_row_id
    managed_case.current_cycle_started_at = cycle_started_at
    managed_case.last_managed_at = now
    managed_case.last_managed_by_user_id = actor.id
    managed_case.last_note = normalized_note
    managed_case.confirmed_work_order_number = work_order_number
    managed_case.updated_at = now
    action = NavifaultManagedFaultAction(
        case_id=managed_case.case_id,
        action_type="managed",
        managed_at=now,
        managed_through_at=latest_occurrence_at,
        managed_through_row_id=checkpoint_row_id,
        cycle_started_at=cycle_started_at,
        actor_user_id=actor.id,
        note=normalized_note,
        work_order_number=work_order_number,
        details=details,
    )
    session.add(action)
    await session.flush()
    return managed_case, action


async def unmark_fault_managed(
    session: AsyncSession,
    *,
    context: ManagementFaultContext,
    actor: User | None,
    note: str | None,
) -> tuple[NavifaultManagedFaultCase | None, NavifaultManagedFaultAction | None]:
    """Revierte una gestión sin borrar su evidencia de auditoría.

    La firma vuelve a ``pending`` al retirar la marca de agua. Se conserva el
    caso y se anexa una acción ``unmanaged``: así un gestor puede corregir un
    clic equivocado sin perder quién ni cuándo se gestionó originalmente.
    """

    managed_case = await session.scalar(
        select(NavifaultManagedFaultCase)
        .where(
            NavifaultManagedFaultCase.vehicle_id == context.vehicle.id,
            NavifaultManagedFaultCase.signature_sha256 == context.signature.sha256,
        )
        .with_for_update()
    )
    if managed_case is None or managed_case.managed_through_at is None:
        return managed_case, None

    now = datetime.now(UTC)
    normalized_note = note.strip() if note and note.strip() else None
    reverted_through_at = managed_case.managed_through_at
    reverted_through_row_id = managed_case.managed_through_row_id
    reverted_cycle_started_at = managed_case.current_cycle_started_at
    managed_case.managed_through_at = None
    managed_case.managed_through_row_id = None
    managed_case.current_cycle_started_at = None
    managed_case.confirmed_work_order_number = None
    managed_case.updated_at = now
    action = NavifaultManagedFaultAction(
        case_id=managed_case.case_id,
        action_type="unmanaged",
        managed_at=now,
        managed_through_at=reverted_through_at,
        managed_through_row_id=reverted_through_row_id,
        cycle_started_at=reverted_cycle_started_at,
        # Sin autor cuando la reversión la hizo el sistema al observar que la
        # orden dejó de sostener la gestión: atribuírsela a una persona sería
        # falsear quién decidió qué.
        actor_user_id=actor.id if actor is not None else None,
        note=normalized_note,
    )
    session.add(action)
    await session.flush()
    return managed_case, action


async def list_management_states_for_fault_rows(
    session: AsyncSession,
    *,
    fault_row_ids: list[str],
    fleet_ids: list[uuid.UUID],
) -> list[dict[str, Any]]:
    """Devuelve el estado de gestión de las filas que Navifault ya muestra.

    Es una consulta única para la página actual de la tabla; no duplica el
    listado de Navifault ni permite que un gestor consulte filas fuera de sus
    flotas. Una fila posterior al checkpoint aparece como ``repeated``.
    """

    unique_row_ids = list(dict.fromkeys(fault_row_ids))
    if not unique_row_ids or not fleet_ids:
        return []

    fact = FactFaultEvent
    managed_case = NavifaultManagedFaultCase
    first_reappearance_at = _first_reappearance_expression(fact, managed_case)
    management_state = _management_state_expression(
        managed_case, first_reappearance_at
    ).label("status")
    management_note = case(
        (
            _management_state_expression(managed_case, first_reappearance_at).in_(
                ("managed", "repeated", "escalada", "pendiente_registro")
            ),
            managed_case.last_note,
        ),
        else_=None,
    ).label("last_note")
    # El enlace a la novedad sólo se publica mientras el escalamiento GOBIERNA
    # el estado. En `managed` la falla se puede volver a escalar —el guard lo
    # permite en cuanto hay gestión—, así que publicarlo ahí haría que la
    # pantalla escondiera un botón que sí debe ofrecerse.
    escalacion_vigente = _management_state_expression(
        managed_case, first_reappearance_at
    ).in_(("escalada", "pendiente_registro"))
    escalated_novedad_id = case(
        (escalacion_vigente, managed_case.escalated_novedad_id), else_=None
    ).label("escalated_novedad_id")
    escalated_issue_number = case(
        (escalacion_vigente, Novedad.cloudfleet_issue_number), else_=None
    ).label("escalated_issue_number")
    # La orden que el portal ya asocia a la falla, para que el panel no la pida.
    # Dos orígenes y ninguno se inventa: si hay gestión confirmada, la orden que
    # la sostiene; si el escalamiento gobierna el estado, la que el taller le
    # puso a la novedad. Fuera de esos dos casos va `null` a propósito — la
    # orden de un ciclo anterior no describe el ciclo vigente.
    work_order_number = case(
        (
            _management_state_expression(managed_case, first_reappearance_at).in_(
                ("managed", "repeated")
            ),
            managed_case.confirmed_work_order_number,
        ),
        (escalacion_vigente, Novedad.external_work_order_number),
        else_=None,
    ).label("work_order_number")
    statement = (
        select(
            fact.row_id.label("fault_row_id"),
            management_state,
            management_note,
            escalated_novedad_id,
            escalated_issue_number,
            work_order_number,
            # Para derivar la referencia hace falta la misma identidad con la
            # que el caso es único, más su ciclo vigente.
            Vehicle.id.label("_vehicle_id"),
            managed_case.cycle_number.label("_cycle_number"),
            # Sin vehículo en CloudFleet no se le puede crear una novedad: el
            # proveedor rechaza el alta. Se comprueba aquí para no ofrecer un
            # botón que va a fallar después de que la persona escriba el
            # comentario. Es una réplica local, así que no cuesta una petición.
            select(CloudfleetVehicle.id)
            .where(CloudfleetVehicle.code == Vehicle.plate)
            .correlate(Vehicle)
            .exists()
            .label("vehicle_in_cloudfleet"),
            _normalized_source_expression(fact).label("_source"),
            fact.codigo_diagnostico.label("_diagnostic_code"),
            fact.codigo_modo_de_falla.label("_failure_mode"),
            fact.luz_de_parada_amber.label("_stop_amber"),
            fact.luz_de_parada_roja.label("_stop_red"),
            fact.lampara_de_averia.label("_malfunction"),
            fact.lampara_de_advertencia.label("_warning"),
        )
        .select_from(fact)
        .join(DimVehicle, DimVehicle.vehicle_id == fact.vehicle_id)
        .join(
            GeotabDatabase,
            func.lower(GeotabDatabase.database_key) == func.lower(DimVehicle.database_name),
        )
        .join(
            Vehicle,
            and_(
                Vehicle.geotab_database_id == GeotabDatabase.id,
                Vehicle.geotab_device_id == DimVehicle.device_id,
                Vehicle.plate == fact.movil,
                Vehicle.is_active.is_(True),
                Vehicle.fleet_id.in_(fleet_ids),
            ),
        )
        .outerjoin(managed_case, and_(*_signature_join_conditions(fact, managed_case, Vehicle)))
        .outerjoin(Novedad, Novedad.id == managed_case.escalated_novedad_id)
        .where(
            fact.row_id.in_(unique_row_ids),
            _normalized_diagnostic_expression(fact).not_like("unknown%"),
        )
    )
    filas: list[dict[str, Any]] = []
    for row in (await session.execute(statement)).mappings():
        datos = {k: v for k, v in row.items() if not k.startswith("_")}
        firma = ManagedFaultSignature(
            source=str(row["_source"] or "").strip(),
            diagnostic_code=row["_diagnostic_code"],
            failure_mode=row["_failure_mode"],
            stop_amber=row["_stop_amber"],
            stop_red=row["_stop_red"],
            malfunction=row["_malfunction"],
            warning=row["_warning"],
        )
        datos["navifault_reference"] = navifault_reference(
            vehicle_id=row["_vehicle_id"],
            # Sin caso, la falla está en su primer ciclo: no ha habido gestión,
            # así que no hay nada que reiniciar.
            cycle_number=row["_cycle_number"] or 1,
            signature=firma,
        )
        # Misma función que decide si el cierre con nota es legítimo, para que
        # la pantalla no pueda ofrecer una vía que el endpoint va a rechazar.
        datos["is_telematics"] = is_telematics_fault(firma)
        filas.append(datos)
    return filas


#: Estados en los que la orden de CloudFleet ya no va a producir más trabajo:
#: el vehículo se intervino y sólo falta declarar qué se hizo. `voided` NO entra
#: —anular devuelve la novedad a abierta y vacía la orden, comprobado el
#: 2026-09-05— y `opened` tampoco: el taller la tiene pero no ha terminado.
WORK_ORDER_FINISHED_STATUSES = ("closed", "onTechnicalCompletion")


def _escalation_finished_expression(managed_case: Any) -> Any:
    """¿El taller TERMINÓ el trabajo de la novedad que persigue esta falla?

    No basta con `external_is_done`. Comprobado contra el proveedor el
    2026-09-05: asignar una novedad a una orden la marca hecha en el acto,
    aunque la orden siga abierta y el vehículo sin intervenir. Preguntar sólo
    por ese campo dejaba la falla en "pendiente de registro" pidiendo declarar
    un desenlace que todavía no había ocurrido, y además la desbloqueaba para
    volver a escalarla mientras el taller trabajaba.

    Lo que sí lo distingue es el estado de la ORDEN, que el sync replica junto
    al de la novedad.

    Se resuelve aquí dentro y no en cada llamador a propósito: los dos caminos
    de la bandeja tienen que decidir el estado con la MISMA regla, y nada más
    los obligaría a coincidir.
    """

    return (
        select(
            and_(
                Novedad.external_is_done.is_(True),
                Novedad.external_work_order_status.in_(WORK_ORDER_FINISHED_STATUSES),
            )
        )
        .where(Novedad.id == managed_case.escalated_novedad_id)
        .correlate(managed_case)
        .scalar_subquery()
    )


def _escalation_gone_expression(managed_case: Any) -> Any:
    """¿La novedad que perseguía la falla ya no existe en CloudFleet?

    Una novedad borrada deja de gobernar el estado: no hay nada persiguiendo la
    falla y se puede escalar de nuevo. Sin esto la fila decía "Escalada" y el
    guard permitía re-escalar al mismo tiempo — la insignia contradecía la regla.
    """

    return (
        select(Novedad.external_deleted_at.is_not(None))
        .where(Novedad.id == managed_case.escalated_novedad_id)
        .correlate(managed_case)
        .scalar_subquery()
    )


def _management_state_expression(managed_case: Any, first_reappearance_at: Any) -> Any:
    """Estado actual según la ventana de reincidencia acordada.

    Si la primera reaparición llega dentro de 30 días, conserva el estado
    ``repeated``. Después de ese plazo el caso anterior queda como antecedente,
    pero la falla vuelve a la bandeja como ``pending`` para un ciclo nuevo.

    Las dos ramas del escalamiento exigen ``managed_through_at IS NULL``, y eso
    es lo que separa los ciclos sin necesidad de limpiar columnas ni de una
    tabla de ciclos: un escalamiento deja de gobernar el estado en cuanto hay
    gestión, y una reaparición posterior a los 30 días no lo resucita porque no
    toca esa columna.
    """

    escalada = and_(
        managed_case.escalated_novedad_id.is_not(None),
        managed_case.managed_through_at.is_(None),
        # Una novedad borrada en CloudFleet no gobierna nada: el escalamiento
        # deja de contar y la falla vuelve al ciclo normal, escalable de nuevo.
        _escalation_gone_expression(managed_case).is_not(True),
    )
    return case(
        (managed_case.case_id.is_(None), literal("pending")),
        # El taller TERMINÓ su orden y nadie declaró el desenlace en Navifault.
        # No es "resuelta": la orden dice el trabajo, su sistema y sus repuestos,
        # pero una orden cubre varios trabajos y el desenlace es una decisión de
        # persona. La falla sigue sin gestionar y visible.
        (
            and_(escalada, _escalation_finished_expression(managed_case).is_(True)),
            literal("pendiente_registro"),
        ),
        (escalada, literal("escalada")),
        (managed_case.managed_through_at.is_(None), literal("pending")),
        (
            and_(
                first_reappearance_at.is_not(None),
                first_reappearance_at <= managed_case.last_managed_at + REPEAT_WINDOW,
            ),
            literal("repeated"),
        ),
        (first_reappearance_at.is_not(None), literal("pending")),
        else_=literal("managed"),
    )


async def get_management_summary(
    session: AsyncSession, *, fleet_ids: list[uuid.UUID]
) -> dict[str, int | None]:
    """Devuelve KPIs de estado y respuesta de gestión dentro de la flota.

    Se cuentan casos (vehículo + firma), no filas ni ocurrencias agregadas de
    Geotab. Por ello el KPI no se infla cuando la misma falla reporta miles de
    eventos antes o después de haber sido atendida. El tiempo promedio se
    calcula por ciclos cerrados válidos: una acción ``managed`` seguida de
    ``unmanaged`` fue corregida y no entra al promedio.
    """

    if not fleet_ids:
        return {
            "managed_faults": 0,
            "repeated_faults": 0,
            "escalated_faults": 0,
            "average_management_seconds": None,
            "management_time_sample_size": 0,
            "average_escalation_seconds": None,
            "escalation_time_sample_size": 0,
            "management_time_window_days": MANAGEMENT_TIME_WINDOW_DAYS,
        }

    fact = FactFaultEvent
    managed_case = NavifaultManagedFaultCase
    first_reappearance_at = func.min(fact.fecha_de_falla).filter(
        fact.fecha_de_falla > managed_case.managed_through_at,
        fact.fecha_de_falla > managed_case.last_managed_at,
    )
    management_state = _management_state_expression(managed_case, first_reappearance_at).label(
        "status"
    )
    case_states = (
        select(managed_case.case_id.label("case_id"), management_state)
        .select_from(fact)
        .join(DimVehicle, DimVehicle.vehicle_id == fact.vehicle_id)
        .join(
            GeotabDatabase,
            func.lower(GeotabDatabase.database_key) == func.lower(DimVehicle.database_name),
        )
        .join(
            Vehicle,
            and_(
                Vehicle.geotab_database_id == GeotabDatabase.id,
                Vehicle.geotab_device_id == DimVehicle.device_id,
                Vehicle.plate == fact.movil,
                Vehicle.is_active.is_(True),
                Vehicle.fleet_id.in_(fleet_ids),
            ),
        )
        .join(managed_case, and_(*_signature_join_conditions(fact, managed_case, Vehicle)))
        .where(_normalized_diagnostic_expression(fact).not_like("unknown%"))
        .group_by(
            managed_case.case_id,
            managed_case.managed_through_at,
            managed_case.last_managed_at,
        )
        .subquery()
    )
    statement = select(
        func.count().filter(case_states.c.status == "managed").label("managed_faults"),
        func.count().filter(case_states.c.status == "repeated").label("repeated_faults"),
        # Los dos estados del escalamiento cuentan como "escalada": en los dos
        # hay una novedad persiguiendo la falla y nadie ha confirmado nada. Lo
        # que los separa es si el taller terminó, que interesa en la fila, no
        # en el indicador.
        func.count()
        .filter(case_states.c.status.in_(("escalada", "pendiente_registro")))
        .label("escalated_faults"),
    )
    row = (await session.execute(statement)).mappings().one()

    action = NavifaultManagedFaultAction
    action_timeline = (
        select(
            action.case_id,
            action.action_type,
            action.managed_at,
            action.cycle_started_at,
            func.lead(action.action_type)
            .over(
                partition_by=action.case_id,
                order_by=(action.managed_at.asc(), action.action_id.asc()),
            )
            .label("next_action_type"),
        )
        .subquery()
    )
    response_seconds = func.extract(
        "epoch", action_timeline.c.managed_at - action_timeline.c.cycle_started_at
    )
    timing_statement = (
        select(
            func.count().label("sample_size"),
            func.avg(response_seconds).label("average_seconds"),
        )
        .select_from(action_timeline)
        .join(managed_case, managed_case.case_id == action_timeline.c.case_id)
        .join(
            Vehicle,
            and_(
                Vehicle.id == managed_case.vehicle_id,
                Vehicle.is_active.is_(True),
                Vehicle.fleet_id.in_(fleet_ids),
            ),
        )
        .where(
            action_timeline.c.action_type == "managed",
            action_timeline.c.cycle_started_at.is_not(None),
            action_timeline.c.next_action_type.is_distinct_from("unmanaged"),
            action_timeline.c.managed_at >= datetime.now(UTC) - timedelta(days=MANAGEMENT_TIME_WINDOW_DAYS),
            action_timeline.c.managed_at >= action_timeline.c.cycle_started_at,
        )
    )
    timing = (await session.execute(timing_statement)).mappings().one()
    average_seconds = timing["average_seconds"]

    # Tiempo de escalamiento: de la aparición de la falla a que llegó al taller.
    # Se mide igual que el de gestión y sobre la misma bitácora; lo único que
    # cambia es la acción que cierra el intervalo. Una escalada que después se
    # gestiona sigue contando: son dos tramos del mismo ciclo, no alternativas.
    escalation_statement = (
        select(
            func.count().label("sample_size"),
            func.avg(response_seconds).label("average_seconds"),
        )
        .select_from(action_timeline)
        .join(managed_case, managed_case.case_id == action_timeline.c.case_id)
        .join(
            Vehicle,
            and_(
                Vehicle.id == managed_case.vehicle_id,
                Vehicle.is_active.is_(True),
                Vehicle.fleet_id.in_(fleet_ids),
            ),
        )
        .where(
            action_timeline.c.action_type == "escalated",
            action_timeline.c.cycle_started_at.is_not(None),
            action_timeline.c.managed_at
            >= datetime.now(UTC) - timedelta(days=MANAGEMENT_TIME_WINDOW_DAYS),
            action_timeline.c.managed_at >= action_timeline.c.cycle_started_at,
        )
    )
    escalation = (await session.execute(escalation_statement)).mappings().one()
    escalation_seconds = escalation["average_seconds"]

    return {
        "managed_faults": int(row["managed_faults"] or 0),
        "repeated_faults": int(row["repeated_faults"] or 0),
        "escalated_faults": int(row["escalated_faults"] or 0),
        "average_management_seconds": (
            round(float(average_seconds)) if average_seconds is not None else None
        ),
        "management_time_sample_size": int(timing["sample_size"] or 0),
        "average_escalation_seconds": (
            round(float(escalation_seconds)) if escalation_seconds is not None else None
        ),
        "escalation_time_sample_size": int(escalation["sample_size"] or 0),
        "management_time_window_days": MANAGEMENT_TIME_WINDOW_DAYS,
    }


async def list_managed_fault_cases(
    session: AsyncSession,
    *,
    fleet_ids: list[uuid.UUID],
    states: set[FaultManagementState],
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    """Lista casos de gestión aplicando alcance de flota antes de agrupar.

    Hay dos caminos, y la diferencia entre ellos es de orden de magnitud:

    - ``managed`` y ``repeated`` exigen que el caso exista —en cuanto
      ``case_id`` es NULL el estado es ``pending``—, así que se parte de
      ``managed_fault_cases``, que está acotada, y cada caso resuelve sus
      ocurrencias con un LATERAL que entra por
      ``ix_fact_fault_event_scope_time``. El costo es O(casos), no O(histórico);
    - ``pending`` es, por definición, una firma SIN caso: no hay nada acotado
      por donde entrar y toca recorrer el hecho agrupando por firma.

    Ninguno de los dos carga el histórico a Python ni modifica analytics. Un
    caso gestionado sólo entra en ``repeated`` si hay una ocurrencia posterior a
    su marca de agua.
    """

    if not fleet_ids or not states:
        return [], 0
    if "pending" in states:
        return await _list_cases_scanning_facts(
            session, fleet_ids=fleet_ids, states=states, limit=limit, offset=offset
        )
    return await _list_cases_from_managed_cases(
        session, fleet_ids=fleet_ids, states=states, limit=limit, offset=offset
    )


def _case_row(row: Any) -> dict[str, Any]:
    """Fila del contrato de la bandeja, idéntica en los dos caminos."""

    return {
        "case_id": row["case_id"],
        "status": row["status"],
        "vehicle_id": row["portal_vehicle_id"],
        "plate": row["plate"],
        "source": row["nombre_fuente_diagnostico"] or None,
        "diagnostic_code": row["codigo_diagnostico"],
        "failure_mode": row["codigo_modo_de_falla"],
        "diagnostic": row["diagnostic"],
        "failure_mode_name": row["failure_mode_name"],
        "controller": row["controller"],
        "attention_type": row["attention_type"],
        "stop_red": row["luz_de_parada_roja"],
        "stop_amber": row["luz_de_parada_amber"],
        "malfunction": row["lampara_de_averia"],
        "warning": row["lampara_de_advertencia"],
        "first_seen_at": row["first_seen_at"],
        "last_seen_at": row["last_seen_at"],
        "sample_fault_row_id": row["sample_fault_row_id"],
        "analytics_records": int(row["analytics_records"] or 0),
        "reported_occurrences": int(row["reported_occurrences"] or 0),
        "repeated_occurrences": (
            int(row["post_management_occurrences"] or 0) if row["status"] == "repeated" else 0
        ),
        "last_managed_at": row["last_managed_at"],
        "last_managed_by": row["last_managed_by"],
        "last_note": row["last_note"],
        **_escalation_fields(row),
    }


def _escalation_fields(row: Any) -> dict[str, Any]:
    """El enlace a la novedad, publicado en todos los estados del ciclo vigente.

    Se publica también cuando el estado ya es `managed` o `repeated`: la falla y
    la novedad tienen ciclos de vida independientes —gestionar la falla no cierra
    la issue, y cerrar la issue no repara la falla—, así que una fila debe poder
    decir "Gestionada · Novedad #698 abierta". Sin eso, gestionar una falla
    escalada borraría de la pantalla que hubo una orden de trabajo.

    Pero **sólo mientras no haya empezado un ciclo nuevo**. Una firma que
    reaparece más de 30 días después de una gestión abre un ciclo nuevo, y la
    novedad de entonces perseguía la falla anterior, no ésta.

    La regla NO puede ser "el escalamiento es posterior a la última gestión":
    con eso, gestionar una falla escalada borraría el enlace justo cuando más
    hace falta — "Gestionada · Novedad #698 abierta" es la fila que cuenta la
    historia completa. Lo que marca el ciclo nuevo es el estado: `pending`
    **habiendo** una gestión previa sólo ocurre en la rama de reaparición
    tardía.
    """

    escalated_at = row["escalated_at"]
    ciclo_nuevo = row["status"] == "pending" and row["last_managed_at"] is not None
    if escalated_at is None or ciclo_nuevo:
        return {
            "escalated_novedad_id": None,
            "escalated_issue_number": None,
            "escalated_at": None,
            "escalated_by": None,
            "escalated_external_is_done": None,
            "escalated_work_order_number": None,
            "escalated_deleted_at": None,
            "escalated_labor_id": None,
            "escalated_labor_name": None,
            "occurrences_since_escalation": None,
        }
    return {
        "escalated_novedad_id": row["escalated_novedad_id"],
        "escalated_issue_number": row["escalated_issue_number"],
        "escalated_at": escalated_at,
        "escalated_by": row["escalated_by"],
        "escalated_external_is_done": row["escalated_external_is_done"],
        "escalated_work_order_number": row["escalated_work_order_number"],
        # La issue ya no existe en CloudFleet: la fila no puede seguir diciendo
        # "Escalada · abierta". El enlace se conserva porque la falla sí fue
        # escalada, pero el estado deja de gobernarlo.
        "escalated_deleted_at": row["escalated_deleted_at"],
        # Trabajo al que el taller ató la novedad. Sin él, la orden no dice cuál
        # de sus trabajos corresponde a esta falla.
        "escalated_labor_id": row["escalated_labor_id"],
        "escalated_labor_name": row["escalated_labor_name"],
        # `null` si nunca se escaló, nunca 0: no escalado y escalado sin
        # reincidencia no son lo mismo, y 0 borraría la diferencia justo en la
        # columna que sirve para decidir a quién perseguir.
        "occurrences_since_escalation": int(row["post_escalation_occurrences"] or 0),
    }


async def _list_cases_from_managed_cases(
    session: AsyncSession,
    *,
    fleet_ids: list[uuid.UUID],
    states: set[FaultManagementState],
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    """Camino acotado: un caso por fila, sus ocurrencias por LATERAL.

    Se entra por ``managed_fault_cases`` y no por el hecho. La firma ya está
    escrita en el caso, así que el LATERAL filtra por ``vehicle_id`` —cubierto
    por índice— y no hay agrupación sobre el millón de filas del hecho.

    El ``analytics_records > 0`` no es cosmético: un agregado sin GROUP BY
    devuelve una fila aunque no haya ocurrencias, y el camino que agrupa el
    hecho omitiría ese caso por no tener grupo. Sin ese filtro los dos caminos
    dejarían de coincidir para un caso cuyas ocurrencias ya no están en
    analytics.
    """

    fact = FactFaultEvent
    managed_case = NavifaultManagedFaultCase
    manager = User
    escalator = aliased(User)
    occurrence_weight = func.coalesce(fact.recuento_de_fallos, 1)
    after_checkpoint = and_(
        fact.fecha_de_falla > managed_case.managed_through_at,
        fact.fecha_de_falla > managed_case.last_managed_at,
    )
    occurrences = (
        select(
            func.min(fact.fecha_de_falla).label("first_seen_at"),
            func.max(fact.fecha_de_falla).label("last_seen_at"),
            func.max(fact.row_id).label("sample_fault_row_id"),
            func.count().label("analytics_records"),
            func.sum(occurrence_weight).label("reported_occurrences"),
            func.sum(case((after_checkpoint, occurrence_weight), else_=0)).label(
                "post_management_occurrences"
            ),
            # Sin esto, una falla escalada que sigue disparándose todos los días
            # se ve idéntica a una que se calló al escalarla, y ese es el caso
            # urgente. Va DENTRO del mismo LATERAL: las filas ya se están
            # leyendo, así que el coste marginal es cero.
            func.sum(
                case(
                    (
                        and_(
                            managed_case.escalated_at.is_not(None),
                            fact.fecha_de_falla > managed_case.escalated_at,
                        ),
                        occurrence_weight,
                    ),
                    else_=0,
                )
            ).label("post_escalation_occurrences"),
            func.min(fact.fecha_de_falla)
            .filter(after_checkpoint)
            .label("first_reappearance_at"),
            func.max(fact.diagnostico).label("diagnostic"),
            func.max(fact.modo_de_falla).label("failure_mode_name"),
            func.max(fact.nombre_de_controlador).label("controller"),
            func.max(fact.tipo_de_atencion).label("attention_type"),
        )
        .where(
            fact.vehicle_id == DimVehicle.vehicle_id,
            fact.movil == Vehicle.plate,
            _normalized_source_expression(fact) == managed_case.source,
            fact.codigo_diagnostico.is_not_distinct_from(managed_case.diagnostic_code),
            fact.codigo_modo_de_falla.is_not_distinct_from(managed_case.failure_mode),
            fact.luz_de_parada_amber.is_not_distinct_from(managed_case.stop_amber),
            fact.luz_de_parada_roja.is_not_distinct_from(managed_case.stop_red),
            fact.lampara_de_averia.is_not_distinct_from(managed_case.malfunction),
            fact.lampara_de_advertencia.is_not_distinct_from(managed_case.warning),
            _normalized_diagnostic_expression(fact).not_like("unknown%"),
        )
        .correlate(managed_case, DimVehicle, Vehicle)
        .lateral("ocurrencias")
    )
    management_state = _management_state_expression(
        managed_case, occurrences.c.first_reappearance_at
    )
    statement = (
        select(
            managed_case.case_id,
            management_state.label("status"),
            Vehicle.id.label("portal_vehicle_id"),
            Vehicle.plate,
            managed_case.source.label("nombre_fuente_diagnostico"),
            managed_case.diagnostic_code.label("codigo_diagnostico"),
            managed_case.failure_mode.label("codigo_modo_de_falla"),
            occurrences.c.diagnostic,
            occurrences.c.failure_mode_name,
            occurrences.c.controller,
            occurrences.c.attention_type,
            managed_case.stop_red.label("luz_de_parada_roja"),
            managed_case.stop_amber.label("luz_de_parada_amber"),
            managed_case.malfunction.label("lampara_de_averia"),
            managed_case.warning.label("lampara_de_advertencia"),
            occurrences.c.first_seen_at,
            occurrences.c.last_seen_at,
            occurrences.c.sample_fault_row_id,
            occurrences.c.analytics_records,
            occurrences.c.post_escalation_occurrences,
            occurrences.c.reported_occurrences,
            occurrences.c.post_management_occurrences,
            managed_case.last_managed_at,
            manager.full_name.label("last_managed_by"),
            managed_case.escalated_novedad_id,
            managed_case.escalated_at,
            escalator.full_name.label("escalated_by"),
            Novedad.cloudfleet_issue_number.label("escalated_issue_number"),
            Novedad.external_is_done.label("escalated_external_is_done"),
            Novedad.external_work_order_number.label("escalated_work_order_number"),
            Novedad.external_deleted_at.label("escalated_deleted_at"),
            Novedad.associated_labor_id.label("escalated_labor_id"),
            Novedad.associated_labor_name.label("escalated_labor_name"),
            managed_case.last_note,
        )
        .select_from(managed_case)
        .join(
            Vehicle,
            and_(
                Vehicle.id == managed_case.vehicle_id,
                Vehicle.is_active.is_(True),
                Vehicle.fleet_id.in_(fleet_ids),
            ),
        )
        .join(GeotabDatabase, GeotabDatabase.id == Vehicle.geotab_database_id)
        .join(
            DimVehicle,
            and_(
                DimVehicle.device_id == Vehicle.geotab_device_id,
                func.lower(DimVehicle.database_name) == func.lower(GeotabDatabase.database_key),
            ),
        )
        .join(occurrences, true())
        .outerjoin(manager, manager.id == managed_case.last_managed_by_user_id)
        .outerjoin(escalator, escalator.id == managed_case.escalated_by_user_id)
        .outerjoin(Novedad, Novedad.id == managed_case.escalated_novedad_id)
        .where(
            occurrences.c.analytics_records > 0,
            or_(*[management_state == state for state in sorted(states)]),
        )
    )
    count_statement = select(func.count()).select_from(statement.subquery())
    # Reincidente primero porque es la señal más urgente. Después lo que ya se
    # cerró en el taller y sólo espera que alguien declare el desenlace: es
    # trabajo de un clic. Después lo escalado, que espera al taller y no a
    # nosotros. `managed` al final, que no pide nada.
    state_priority = case(
        (management_state == "repeated", 0),
        (management_state == "pendiente_registro", 1),
        (management_state == "escalada", 2),
        (management_state == "pending", 3),
        else_=4,
    )
    statement = (
        statement.order_by(
            state_priority, occurrences.c.last_seen_at.desc().nullslast(), Vehicle.plate
        )
        .limit(limit)
        .offset(offset)
    )
    total = (await session.execute(count_statement)).scalar_one()
    result = await session.execute(statement)
    return [_case_row(row) for row in result.mappings()], total


async def _list_cases_scanning_facts(
    session: AsyncSession,
    *,
    fleet_ids: list[uuid.UUID],
    states: set[FaultManagementState],
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    """Camino para ``pending``: recorre el hecho porque no hay caso por donde entrar.

    El join a ``managed_fault_cases`` TIENE que ser externo: una firma pendiente
    es precisamente la que no tiene caso, y un join interno la eliminaría.
    """

    fact = FactFaultEvent
    managed_case = NavifaultManagedFaultCase
    manager = User
    escalator = aliased(User)
    last_seen_at = func.max(fact.fecha_de_falla)
    first_seen_at = func.min(fact.fecha_de_falla)
    first_reappearance_at = func.min(fact.fecha_de_falla).filter(
        fact.fecha_de_falla > managed_case.managed_through_at,
        fact.fecha_de_falla > managed_case.last_managed_at,
    )
    management_state = _management_state_expression(managed_case, first_reappearance_at).label(
        "status"
    )
    occurrence_weight = func.coalesce(fact.recuento_de_fallos, 1)
    post_management_occurrences = func.sum(
        case(
            (
                and_(
                    managed_case.managed_through_at.is_not(None),
                    fact.fecha_de_falla > managed_case.managed_through_at,
                    fact.fecha_de_falla > managed_case.last_managed_at,
                ),
                occurrence_weight,
            ),
            else_=0,
        )
    ).label("post_management_occurrences")
    post_escalation_occurrences = func.sum(
        case(
            (
                and_(
                    managed_case.escalated_at.is_not(None),
                    fact.fecha_de_falla > managed_case.escalated_at,
                ),
                occurrence_weight,
            ),
            else_=0,
        )
    ).label("post_escalation_occurrences")

    statement = (
        select(
            managed_case.case_id,
            management_state,
            Vehicle.id.label("portal_vehicle_id"),
            Vehicle.plate,
            fact.nombre_fuente_diagnostico,
            fact.codigo_diagnostico,
            fact.codigo_modo_de_falla,
            func.max(fact.diagnostico).label("diagnostic"),
            func.max(fact.modo_de_falla).label("failure_mode_name"),
            func.max(fact.nombre_de_controlador).label("controller"),
            func.max(fact.tipo_de_atencion).label("attention_type"),
            fact.luz_de_parada_roja,
            fact.luz_de_parada_amber,
            fact.lampara_de_averia,
            fact.lampara_de_advertencia,
            first_seen_at.label("first_seen_at"),
            last_seen_at.label("last_seen_at"),
            func.max(fact.row_id).label("sample_fault_row_id"),
            func.count().label("analytics_records"),
            func.sum(occurrence_weight).label("reported_occurrences"),
            post_management_occurrences,
            post_escalation_occurrences,
            managed_case.last_managed_at,
            manager.full_name.label("last_managed_by"),
            managed_case.escalated_novedad_id,
            managed_case.escalated_at,
            escalator.full_name.label("escalated_by"),
            Novedad.cloudfleet_issue_number.label("escalated_issue_number"),
            Novedad.external_is_done.label("escalated_external_is_done"),
            Novedad.external_work_order_number.label("escalated_work_order_number"),
            Novedad.external_deleted_at.label("escalated_deleted_at"),
            Novedad.associated_labor_id.label("escalated_labor_id"),
            Novedad.associated_labor_name.label("escalated_labor_name"),
            managed_case.last_note,
        )
        .select_from(fact)
        .join(DimVehicle, DimVehicle.vehicle_id == fact.vehicle_id)
        .join(
            GeotabDatabase,
            func.lower(GeotabDatabase.database_key) == func.lower(DimVehicle.database_name),
        )
        .join(
            Vehicle,
            and_(
                Vehicle.geotab_database_id == GeotabDatabase.id,
                Vehicle.geotab_device_id == DimVehicle.device_id,
                Vehicle.plate == fact.movil,
                Vehicle.is_active.is_(True),
                Vehicle.fleet_id.in_(fleet_ids),
            ),
        )
        .outerjoin(managed_case, and_(*_signature_join_conditions(fact, managed_case, Vehicle)))
        .outerjoin(manager, manager.id == managed_case.last_managed_by_user_id)
        .outerjoin(escalator, escalator.id == managed_case.escalated_by_user_id)
        .outerjoin(Novedad, Novedad.id == managed_case.escalated_novedad_id)
        .where(_normalized_diagnostic_expression(fact).not_like("unknown%"))
        .group_by(
            managed_case.case_id,
            Vehicle.id,
            Vehicle.plate,
            fact.nombre_fuente_diagnostico,
            fact.codigo_diagnostico,
            fact.codigo_modo_de_falla,
            fact.luz_de_parada_roja,
            fact.luz_de_parada_amber,
            fact.lampara_de_averia,
            fact.lampara_de_advertencia,
            managed_case.managed_through_at,
            managed_case.last_managed_at,
            managed_case.last_note,
            manager.full_name,
            managed_case.escalated_novedad_id,
            managed_case.escalated_at,
            escalator.full_name,
            Novedad.cloudfleet_issue_number,
            Novedad.external_is_done,
            Novedad.external_work_order_number,
            # Toda columna de `novedades` que el SELECT publica va también aquí:
            # este camino agrupa el hecho y PostgreSQL la rechaza si falta.
            Novedad.external_deleted_at,
            Novedad.associated_labor_id,
            Novedad.associated_labor_name,
        )
    )

    status_predicates = [management_state == state for state in sorted(states)]
    statement = statement.having(or_(*status_predicates))
    count_statement = select(func.count()).select_from(statement.subquery())

    # Reincidente primero porque es la señal más urgente. Después lo que ya se
    # cerró en el taller y sólo espera que alguien declare el desenlace: es
    # trabajo de un clic. Después lo escalado, que espera al taller y no a
    # nosotros. `managed` al final, que no pide nada.
    state_priority = case(
        (management_state == "repeated", 0),
        (management_state == "pendiente_registro", 1),
        (management_state == "escalada", 2),
        (management_state == "pending", 3),
        else_=4,
    )
    statement = (
        statement.order_by(state_priority, last_seen_at.desc().nullslast(), Vehicle.plate)
        .limit(limit)
        .offset(offset)
    )
    total = (await session.execute(count_statement)).scalar_one()
    result = await session.execute(statement)
    return [_case_row(row) for row in result.mappings()], total


async def list_managed_fault_actions(
    session: AsyncSession, *, case_id: uuid.UUID
) -> list[dict[str, Any]]:
    action = NavifaultManagedFaultAction
    actor = User
    statement = (
        select(
            action.action_id,
            action.action_type,
            action.managed_at,
            action.managed_through_at,
            action.managed_through_row_id,
            actor.full_name.label("actor_name"),
            action.note,
        )
        .select_from(action)
        .outerjoin(actor, actor.id == action.actor_user_id)
        .where(action.case_id == case_id)
        .order_by(action.managed_at.desc(), action.action_id.desc())
    )
    return [dict(row) for row in (await session.execute(statement)).mappings()]
