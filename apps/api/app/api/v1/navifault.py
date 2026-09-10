from __future__ import annotations

import logging
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import (
    CurrentUser,
    ReportFleetIds,
    require_permission,
    require_platform_admin,
)
from app.db.session import get_db
from app.models.master_data import Vehicle
from app.models.navifault import NavifaultFaultPage, NavifaultManagedFaultCase
from app.models.user import User
from app.schemas.navifault import (
    ClientDescriptionStatus,
    NavifaultClientDescriptionResponse,
    NavifaultConfirmRequest,
    NavifaultCorpusDocumentResponse,
    NavifaultEscalateFaultRequest,
    NavifaultEscalationResponse,
    NavifaultFaultCandidate,
    NavifaultFaultCandidatesResponse,
    NavifaultFaultEvidenceResponse,
    NavifaultFaultManagementStateRead,
    NavifaultFaultManagementStatesRequest,
    NavifaultManagedFaultActionRead,
    NavifaultManagedFaultCaseRead,
    NavifaultManagedFaultCasesResponse,
    NavifaultManagementSummary,
    NavifaultOrderWorkRequest,
    NavifaultOrderWorkResponse,
    NavifaultOriginalFaultDocumentResponse,
    NavifaultUnmarkFaultManagedRequest,
)
from app.services import (
    navifault_client_description_service,
    navifault_document_service,
    navifault_escalation_service,
    navifault_llm_service,
    navifault_management_service,
    navifault_order_service,
)
from app.services.cloudfleet_service import CloudfleetError, CloudfleetVehicleNotFoundError
from app.services.fleet_service import user_can_access_fleet
from app.services.rbac import user_has_permission, user_is_admin

log = logging.getLogger(__name__)

router = APIRouter(prefix="/navifault", tags=["navifault"])

_MANAGEMENT_STATES = {"pending", "escalada", "pendiente_registro", "repeated", "managed"}

#: Lectura del corpus técnico Cummins: documento original, resumen oficial y
#: candidatas de resolución. Es una capacidad de Navitrans, no del cliente, que
#: sólo recibe la comunicación redactada para él.
#:
#: Permiso propio y no `navifault.edit` —que hoy daría exactamente la misma
#: frontera— porque sería un permiso de escritura gobernando una lectura, y eso
#: se "limpia" más adelante sin saber por qué estaba así. Sembrado por la
#: migración `a4b5c6d70053` para `admin` y `admin_flota_navitrans`.
CORPUS_PERMISSION = "navifault_corpus.view"


def build_client_description_response(
    *,
    status: str,
    row: Any,
    resolution: Any,
    cached: bool,
    include_email_variant: bool,
) -> NavifaultClientDescriptionResponse:
    """Arma la respuesta de la comunicación cliente, ocultando el borrador de correo.

    El módulo genera DOS textos: el de plataforma, que es lo que Navifault le
    muestra al cliente, y el de correo, redactado para un envío que todavía no
    existe. Mostrarle al cliente dentro del portal un texto pensado para otro
    medio confunde qué le está comunicando la plataforma, así que la variante de
    correo viaja sólo a usuarios internos.

    No es un permiso aparte: es la misma frontera de `CORPUS_PERMISSION`, porque
    es la misma pregunta —¿este actor es Navitrans?—. Se resuelve en una función
    propia para que la decisión sea comprobable sin sembrar una generación
    completa del proveedor.
    """

    return NavifaultClientDescriptionResponse(
        status=cast(ClientDescriptionStatus, status),
        descripcion_correo_cliente=(
            row.descripcion_correo_cliente if include_email_variant else None
        ),
        descripcion_plataforma_cliente=row.descripcion_plataforma_cliente,
        fault_page_id=resolution.page.fault_page_id,
        pub_id=resolution.page.pub_id,
        fault_code=resolution.page.fault_code,
        variant=resolution.page.variant,
        prompt_version=row.prompt_version,
        model_version=row.model_version,
        cached=cached,
    )


async def _direct_fault_page_for_outcome(
    db: AsyncSession, fault_row_id: str
) -> tuple[str | None, str]:
    """Relaciona evidencia entre flotas únicamente si hay una FC inequívoca."""

    try:
        context = await navifault_llm_service.get_analytics_fault_context(db, fault_row_id)
        fault = navifault_llm_service.fault_input_from_analytics(context)
        resolution = await navifault_llm_service.find_fault_page_candidates(db, fault)
    except navifault_llm_service.NavifaultResolutionError:
        return None, "unavailable"
    if len(resolution.pages) == 1:
        return resolution.pages[0].fault_page_id, "direct"
    if resolution.pages:
        return None, "ambiguous"
    return None, "no_match"


def _candidate_from_page(page: NavifaultFaultPage) -> NavifaultFaultCandidate:
    summary = page.summary if isinstance(page.summary, dict) else {}
    return NavifaultFaultCandidate(
        fault_page_id=page.fault_page_id,
        pub_id=page.pub_id,
        language=page.language,
        engine_model=page.engine_model,
        fault_code=page.fault_code,
        variant=page.variant,
        title=page.title,
        reason=summary.get("Razon") or summary.get("razon"),
        effect=summary.get("efecto") or summary.get("Efecto"),
    )


@router.get(
    "/fault-candidates/{fault_row_id}",
    response_model=NavifaultFaultCandidatesResponse,
    dependencies=[Depends(require_permission(CORPUS_PERMISSION))],
)
async def fault_candidates(
    fault_row_id: str,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> NavifaultFaultCandidatesResponse:
    """Devuelve candidatas Cummins oficiales sin invocar ni encolar IA."""
    try:
        context = await navifault_llm_service.get_analytics_fault_context(db, fault_row_id)
    except navifault_llm_service.NavifaultResolutionError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Falla no encontrada") from exc

    vehicle = context.vehicle
    if (
        not vehicle.is_active
        or vehicle.fleet_id is None
        or (not user_is_admin(user) and not user_can_access_fleet(user, vehicle.fleet_id))
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Falla no encontrada")

    try:
        fault = navifault_llm_service.fault_input_from_analytics(context)
        resolution = await navifault_llm_service.find_fault_page_candidates(db, fault)
    except navifault_llm_service.NavifaultResolutionError as exc:
        return NavifaultFaultCandidatesResponse(
            status="unavailable",
            detail=str(exc),
        )

    candidates = [_candidate_from_page(page) for page in resolution.pages]
    if len(candidates) == 1:
        return NavifaultFaultCandidatesResponse(
            status="matched",
            detail="Coincidencia exacta por manual, protocolo, código y FMI.",
            candidates=candidates,
        )
    if candidates:
        return NavifaultFaultCandidatesResponse(
            status="ambiguous",
            detail=(
                "La llave protocolaria devuelve varias páginas Cummins; se muestran todas "
                "sin escoger una por inferencia."
            ),
            candidates=candidates,
        )
    return NavifaultFaultCandidatesResponse(
        status="no_match",
        detail="No hay una FC Cummins exacta para la llave protocolaria de este manual.",
    )


@router.post(
    "/client-description/{fault_row_id}",
    response_model=NavifaultClientDescriptionResponse,
    dependencies=[Depends(require_permission("navifault.view"))],
)
async def client_description(
    fault_row_id: str,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> NavifaultClientDescriptionResponse:
    """Encola o reutiliza la comunicación cliente de una FC directa y única."""
    try:
        context = await navifault_llm_service.get_analytics_fault_context(db, fault_row_id)
    except navifault_llm_service.NavifaultResolutionError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Falla no encontrada") from exc

    vehicle = context.vehicle
    if (
        not vehicle.is_active
        or vehicle.fleet_id is None
        or (not user_is_admin(user) and not user_can_access_fleet(user, vehicle.fleet_id))
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Falla no encontrada")

    try:
        resolution = await navifault_llm_service.resolve_fault_page(
            db, navifault_llm_service.fault_input_from_analytics(context)
        )
    except navifault_llm_service.NavifaultResolutionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not settings.navifault_llm_enabled or not settings.navifault_llm_base_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El proveedor LLM de Navifault no está habilitado",
        )

    description_status, row, cached = await navifault_client_description_service.get_or_enqueue(
        db, resolution
    )
    return build_client_description_response(
        status=description_status,
        row=row,
        resolution=resolution,
        cached=cached,
        include_email_variant=user_has_permission(user, CORPUS_PERMISSION),
    )


@router.get(
    "/corpus-document/{kind}/{document_id}",
    response_model=NavifaultCorpusDocumentResponse,
    dependencies=[Depends(require_permission(CORPUS_PERMISSION))],
)
async def corpus_document(
    kind: str,
    document_id: str,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> NavifaultCorpusDocumentResponse:
    """Un documento del corpus alcanzado desde un enlace del visor.

    Exige el MISMO permiso que la FC original —es contenido del manual—, y no el
    alcance de flota: aquí no hay falla ni vehículo, sólo documentación técnica
    de Cummins. Quien tiene corpus puede leer el manual entero; el alcance de
    flota gobierna qué FALLAS se ven, y eso ya se comprobó al abrir la ficha.
    """

    del user  # el permiso lo resuelve la dependencia; la identidad no se usa
    try:
        rendered = await navifault_document_service.render_corpus_document(
            db, kind=kind, document_id=document_id
        )
    except navifault_document_service.NavifaultDocumentError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return NavifaultCorpusDocumentResponse(
        kind=rendered.kind,
        document_id=rendered.document_id,
        title=rendered.title,
        html=rendered.html,
        embedded_images=rendered.embedded_images,
        missing_images=rendered.missing_images,
    )


@router.get(
    "/original-document/{fault_row_id}",
    response_model=NavifaultOriginalFaultDocumentResponse,
    dependencies=[Depends(require_permission(CORPUS_PERMISSION))],
)
async def original_fault_document(
    fault_row_id: str,
    user: CurrentUser,
    fault_page_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> NavifaultOriginalFaultDocumentResponse:
    """Devuelve una FC oficial autocontenida para el visor técnico privado."""
    try:
        context = await navifault_llm_service.get_analytics_fault_context(db, fault_row_id)
    except navifault_llm_service.NavifaultResolutionError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Falla no encontrada") from exc

    vehicle = context.vehicle
    if (
        not vehicle.is_active
        or vehicle.fleet_id is None
        or (not user_is_admin(user) and not user_can_access_fleet(user, vehicle.fleet_id))
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Falla no encontrada")

    try:
        if fault_page_id:
            rendered = await navifault_document_service.render_fault_page_by_id(db, fault_page_id)
        else:
            fault_input = navifault_llm_service.fault_input_from_analytics(context)
            candidates_res = await navifault_llm_service.find_fault_page_candidates(db, fault_input)
            if not candidates_res.pages:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No hay una FC Cummins disponible para esta falla en el corpus privado",
                )
            rendered = await navifault_document_service.render_fault_page_model(
                db, candidates_res.pages[0]
            )
    except navifault_document_service.NavifaultDocumentError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except navifault_llm_service.NavifaultResolutionError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return NavifaultOriginalFaultDocumentResponse(
        fault_page_id=rendered.fault_page_id,
        pub_id=rendered.pub_id,
        language=rendered.language,
        fault_code=rendered.fault_code,
        variant=rendered.variant,
        title=rendered.title,
        html=rendered.html,
        embedded_images=rendered.embedded_images,
        missing_images=rendered.missing_images,
    )


@router.get(
    "/management/cases",
    response_model=NavifaultManagedFaultCasesResponse,
    dependencies=[Depends(require_permission("navifault.edit"))],
)
async def managed_fault_cases(
    fleet_ids: ReportFleetIds,
    state: list[str] | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> NavifaultManagedFaultCasesResponse:
    """Bandeja interna: pendientes, repetidas o historial gestionado.

    Los perfiles cliente no pueden consultar este endpoint: exige el permiso de
    edición Navifault, que se asigna sólo a gestores de flota y administradores.
    """

    raw_states = set(state or ["pending", "repeated"])
    invalid = raw_states - _MANAGEMENT_STATES
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Estado de gestión inválido",
        )
    states = cast(set[navifault_management_service.FaultManagementState], raw_states)
    rows, total = await navifault_management_service.list_managed_fault_cases(
        db,
        fleet_ids=fleet_ids,
        states=states,
        limit=limit,
        offset=offset,
    )
    return NavifaultManagedFaultCasesResponse(
        items=[NavifaultManagedFaultCaseRead.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/management/summary",
    response_model=NavifaultManagementSummary,
    dependencies=[Depends(require_permission("navifault.edit"))],
)
async def managed_fault_summary(
    fleet_ids: ReportFleetIds,
    db: AsyncSession = Depends(get_db),
) -> NavifaultManagementSummary:
    """KPIs internos de fallas gestionadas y repetidas para gestores."""

    summary = await navifault_management_service.get_management_summary(
        db, fleet_ids=fleet_ids
    )
    return NavifaultManagementSummary.model_validate(summary)


@router.post(
    "/management/event-states",
    response_model=list[NavifaultFaultManagementStateRead],
    response_model_exclude_none=True,
    dependencies=[Depends(require_permission("navifault.edit"))],
)
async def managed_fault_event_states(
    body: NavifaultFaultManagementStatesRequest,
    fleet_ids: ReportFleetIds,
    db: AsyncSession = Depends(get_db),
) -> list[NavifaultFaultManagementStateRead]:
    """Estado de gestión para la página visible de Navifault."""

    rows = await navifault_management_service.list_management_states_for_fault_rows(
        db,
        fault_row_ids=body.fault_row_ids,
        fleet_ids=fleet_ids,
    )
    return [NavifaultFaultManagementStateRead.model_validate(row) for row in rows]


@router.post(
    "/management/escalate",
    response_model=NavifaultEscalationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def escalate_fault(
    body: NavifaultEscalateFaultRequest,
    user: Annotated[User, Depends(require_platform_admin)],
    db: AsyncSession = Depends(get_db),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> NavifaultEscalationResponse:
    """Lleva la falla a CloudFleet para que el taller la vea en la próxima intervención.

    **Sólo el rol global `admin`**, y no `navifault.edit` + `novedades.edit`. La
    issue que se crea es irreversible —CloudFleet responde 405 a `DELETE`, así
    que una creada por error se queda y hay que cerrarla a mano desde su UI—, y
    es el mismo criterio que ya rige el borrado de novedades.

    Escalar NO gestiona la falla: la reparación no ocurrió, y arrancar sobre eso
    la verificación de 30 días falsearía la efectividad de los desenlaces.
    """

    if settings.cloudfleet_id_reportedby is None:
        # 503 y no 500: es indisponibilidad de configuración del proveedor, no un
        # error del cliente ni un fallo del portal.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "cloudfleet_reporter_not_configured"},
        )

    context = await navifault_management_service.get_management_context(db, body.fault_row_id)
    if (
        context is None
        or context.vehicle.fleet_id is None
        or (not user_is_admin(user) and not user_can_access_fleet(user, context.vehicle.fleet_id))
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Falla no encontrada")

    try:
        novedad, case, created = await navifault_escalation_service.escalate_fault(
            db,
            context=context,
            actor=user,
            priority=body.priority,
            comment=body.comment,
            odometer=body.odometer,
            send_mail=body.send_mail,
            reported_by_id=settings.cloudfleet_id_reportedby,
            idempotency_key=(idempotency_key or "").strip() or None,
        )
    except navifault_escalation_service.NavifaultEscalationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except CloudfleetVehicleNotFoundError as exc:
        # Se registra igual que el error genérico: sin esto no había forma de
        # saber qué placa rechazó el proveedor ni qué dijo.
        log.warning("CloudFleet no reconoce el vehículo %s: %s", context.vehicle.plate, exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "cloudfleet_vehicle_not_found",
                # El mensaje nombra la placa y qué hacer. No lleva el texto
                # crudo del proveedor (SEC-023), pero decirle a la persona que
                # el vehículo no está registrado no revela nada interno y es lo
                # único que le permite resolverlo.
                "message": (
                    f"El vehículo {context.vehicle.plate} no está registrado en CloudFleet, "
                    "así que no se le puede crear una novedad. Regístralo allá y vuelve a "
                    "intentarlo."
                ),
            },
        ) from exc
    except CloudfleetError as exc:
        # El texto crudo del proveedor no sale al cliente (SEC-023).
        log.warning("CloudFleet rechazó el escalamiento de %s: %s", context.vehicle.plate, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "cloudfleet_error",
                "message": (
                    "CloudFleet rechazó la creación de la novedad. Vuelve a intentarlo; "
                    "si persiste, el motivo está en los logs del portal."
                ),
            },
        ) from exc

    # El servicio hace `flush`; `get_db` no commitea al salir, así que sin esto
    # el escalamiento se perdería en silencio con la issue ya creada.
    await db.commit()
    assert case.escalated_at is not None
    return NavifaultEscalationResponse(
        novedad_id=novedad.id,
        cloudfleet_issue_number=novedad.cloudfleet_issue_number,
        status="escalada",
        escalated_at=case.escalated_at,
        created=created,
    )


def _order_work_payload(encontrado: Any) -> dict[str, Any]:
    """Traduce el resultado del servicio al contrato, sin lógica propia."""

    return {
        "work_order_number": encontrado.work_order_number,
        "work_order_status": encontrado.status,
        "work_order_finished": encontrado.is_finished,
        "reference": encontrado.reference,
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
            for labor in encontrado.labors
        ],
    }


async def _management_context_or_404(db: AsyncSession, *, fault_row_id: str, user: Any) -> Any:
    """Contexto de la falla dentro del alcance del actor.

    404 y no 403: confirmar que la falla existe le diría a alguien sin alcance
    qué tiene la flota del vecino.
    """

    context = await navifault_management_service.get_management_context(db, fault_row_id)
    if (
        context is None
        or context.vehicle.fleet_id is None
        or (not user_is_admin(user) and not user_can_access_fleet(user, context.vehicle.fleet_id))
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Falla no encontrada")
    return context


@router.get(
    "/management/evidence/{fault_row_id}",
    response_model=NavifaultFaultEvidenceResponse,
    dependencies=[Depends(require_permission("navifault.edit"))],
)
async def read_fault_evidence(
    fault_row_id: str,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> NavifaultFaultEvidenceResponse:
    """Qué se hizo con esta misma falla en otros vehículos.

    Es de lectura y no cambia nada, así que va por GET — a diferencia de
    `order-work`, que consulta la orden y es una acción sobre la falla.

    El alcance de flota gobierna el acceso a la FALLA: sin él, 404. Lo que la
    respuesta publica ya no tiene dueño —ni placa, ni flota, ni autor—, y por
    eso puede cruzar flotas sin filtrar por el alcance del usuario.
    """

    context = await _management_context_or_404(db, fault_row_id=fault_row_id, user=user)
    filas = await navifault_management_service.fault_evidence(db, context=context)
    return NavifaultFaultEvidenceResponse(
        entries=[
            {
                "note": fila["note"],
                "from_work_order": fila["work_order_number"] is not None,
                # La copia guardada al confirmar: anular la orden borra sus
                # trabajos, así que la evidencia no puede depender de ella.
                "labors": (fila["details"] or {}).get("labors") or [],
                "managed_at": fila["managed_at"],
                "verification": fila["verification"],
            }
            for fila in filas
        ],
        verification_window_days=navifault_management_service.REPEAT_WINDOW_DAYS,
    )


@router.post(
    "/management/order-work",
    response_model=NavifaultOrderWorkResponse,
    dependencies=[Depends(require_permission("navifault.edit"))],
)
async def read_order_work(
    body: NavifaultOrderWorkRequest,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> NavifaultOrderWorkResponse:
    """Qué registró el taller para esta falla en esa orden, sin gestionar nada.

    Es POST y no GET porque consultar la orden es una acción sobre la falla: si
    ya está en un ciclo nuevo, el ciclo avanza y con él la referencia que se
    busca.
    """

    context = await _management_context_or_404(db, fault_row_id=body.fault_row_id, user=user)
    try:
        encontrado = await navifault_order_service.preview_fault_work(
            db, context=context, work_order_number=body.work_order_number
        )
    except navifault_order_service.NavifaultOrderError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CloudfleetError as exc:
        # El texto crudo del proveedor no sale al cliente (SEC-023).
        log.warning("navifault_order_work_provider_error", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No fue posible consultar la orden en CloudFleet",
        ) from exc
    await db.commit()
    return NavifaultOrderWorkResponse.model_validate(_order_work_payload(encontrado))


@router.post(
    "/management/telematics-note",
    response_model=NavifaultManagedFaultActionRead,
    dependencies=[
        Depends(require_permission("navifault.edit")),
        Depends(require_permission("navifault_management.edit")),
    ],
)
async def close_telematics_fault(
    body: NavifaultUnmarkFaultManagedRequest,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> NavifaultManagedFaultActionRead:
    """Cierra con una nota una falla del propio equipo telemático.

    Única excepción al "sin orden no hay gestión". Estas fallas no son del
    vehículo, así que no pueden tener una orden de trabajo suya: exigirla
    obligaría a abrir órdenes por trabajo que nunca se hizo, y no ofrecer nada
    las dejaría abiertas para siempre. La excepción la acota el DATO —la fuente
    `SourceGeotabGoId`— y no una elección del usuario.
    """

    context = await _management_context_or_404(db, fault_row_id=body.fault_row_id, user=user)
    try:
        await navifault_order_service.close_telematics_fault(
            db, context=context, actor=user, note=body.note or ""
        )
    except navifault_order_service.NavifaultConfirmationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    await db.commit()
    accion = await navifault_management_service.latest_action_for_fault(
        db, context=context
    )
    if accion is None:  # pragma: no cover - mark_fault_managed siempre deja acción
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="La falla ya estaba cerrada")
    return NavifaultManagedFaultActionRead.model_validate(accion, from_attributes=True)


@router.post(
    "/management/confirm",
    response_model=NavifaultOrderWorkResponse,
    dependencies=[
        Depends(require_permission("navifault.edit")),
        Depends(require_permission("navifault_management.edit")),
    ],
)
async def confirm_fault_management(
    body: NavifaultConfirmRequest,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> NavifaultOrderWorkResponse:
    """Gestiona la falla confirmando lo que el taller registró.

    Sin un trabajo marcado con la referencia no hay gestión, y no porque una
    validación lo prohíba: no hay nada que confirmar. Ése es el bloqueo que
    impide que el portal afirme algo que la orden no dice.
    """

    context = await _management_context_or_404(db, fault_row_id=body.fault_row_id, user=user)
    try:
        encontrado, _caso = await navifault_order_service.confirm_from_work_order(
            db,
            context=context,
            actor=user,
            work_order_number=body.work_order_number,
            note=body.note,
        )
    except navifault_order_service.NavifaultOrderNotFinishedError as exc:
        # 409 y no 422: la petición está bien formada y la misma funcionará en
        # cuanto el taller cierre la orden. Es un conflicto con el estado.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except navifault_order_service.NavifaultConfirmationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except navifault_order_service.NavifaultOrderError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CloudfleetError as exc:
        log.warning("navifault_confirm_provider_error", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No fue posible consultar la orden en CloudFleet",
        ) from exc
    await db.commit()
    return NavifaultOrderWorkResponse.model_validate(_order_work_payload(encontrado))


@router.get(
    "/management/cases/{case_id}/actions",
    response_model=list[NavifaultManagedFaultActionRead],
    dependencies=[Depends(require_permission("navifault.edit"))],
)
async def managed_fault_case_actions(
    case_id: str,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> list[NavifaultManagedFaultActionRead]:
    """Devuelve la bitácora de una gestión sólo si el caso está en el alcance."""

    import uuid

    try:
        parsed_case_id = uuid.UUID(case_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Caso no encontrado") from exc

    managed_case = await db.get(NavifaultManagedFaultCase, parsed_case_id)
    if managed_case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Caso no encontrado")
    vehicle = await db.get(Vehicle, managed_case.vehicle_id)
    if (
        vehicle is None
        or not vehicle.is_active
        or vehicle.fleet_id is None
        or (not user_is_admin(user) and not user_can_access_fleet(user, vehicle.fleet_id))
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Caso no encontrado")
    rows = await navifault_management_service.list_managed_fault_actions(
        db, case_id=parsed_case_id
    )
    return [NavifaultManagedFaultActionRead.model_validate(row) for row in rows]
