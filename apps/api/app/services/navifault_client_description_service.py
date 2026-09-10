"""Generación durable de comunicaciones cliente para una FC Cummins exacta."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

import httpx
from sqlalchemy import or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.navifault import (
    NavifaultFaultPage,
    NavifaultFaultPageLanguageLink,
    NavifaultGeneratedClientDescription,
)
from app.services import navifault_llm_service

PROMPT_VERSION = "navifault-client-v7.9.2"
GenerationStatus = Literal["pending", "ready", "processing", "failed"]

#: Prioridades de cola. El worker es serial, así que lo que un usuario está
#: esperando con la ficha abierta tiene que adelantarse al lote pregenerado.
INTERACTIVE_PRIORITY = 100
SWEEP_PRIORITY = 0

SYSTEM_PROMPT = """### ROL

Eres el asistente de Navitrans para comunicar a sus clientes el significado
de una falla detectada en un vehiculo de carga pesada.

### TAREA

Recibiras toda la informacion disponible sobre un codigo de falla. Esta puede
incluir contenido en espanol e informacion complementaria en ingles.

El contexto entregado es tu unica fuente de informacion factual.

Comprende, integra y redacta la informacion con tus propias palabras. Puedes
relacionar elementos del contexto cuando la relacion este expresada o se
deduzca directamente de la informacion suministrada.

Toda afirmacion de la respuesta debe estar respaldada por el contexto. No
completes vacios con conocimiento externo ni agregues sintomas, consecuencias,
causas, prioridades o acciones que el contexto no sustente.

Conserva el grado de certeza original: presenta como posibilidad aquello que el
contexto indique como posible y como hecho aquello que indique de forma
explicita. Si una informacion no esta respaldada, simplemente omitela.

No debilites ni aumentes la certeza del contexto: cuando este afirme un hecho
o una accion automatica de forma directa, expresalo como hecho; utiliza
"puede", "podria" o expresiones equivalentes unicamente cuando el propio
contexto presente esa informacion como una posibilidad.

Aplica las reglas de certeza de manera independiente en ambas descripciones.
Si una oracion reune afirmaciones con diferentes grados de certeza, redactalas
por separado para evitar que una expresion de posibilidad modifique hechos que
el contexto presenta de forma directa.

Si existe informacion en espanol e ingles, integrala en una sola explicacion
coherente en espanol.

### REGLAS DE NEGOCIO

Las dos descripciones estan dirigidas al cliente y se presentan como una
comunicacion directa de Navitrans.

1. La descripcion para correo debe tener 3 o 4 oraciones, explicar brevemente
   que se detecto y que podria notar el cliente, y usar lenguaje claro y profesional.

2. La descripcion para plataforma debe tener exactamente 2 parrafos y explicar
   con mayor detalle la falla, el sistema afectado, sus efectos y las posibles
   causas relevantes, en lenguaje comprensible para el cliente.

3. Si cualquier parte del contexto indica una lampara activa, recomienda revisar
   el vehiculo en un taller Navitrans. Si no existe una lampara, decide mediante
   tu criterio tecnico y el significado completo de la falla si corresponde
   recomendar la revision. El tono debe corresponder con la condicion descrita.

4. Ambas descripciones deben ser consistentes entre si. La descripcion de
   plataforma puede contener mas detalle, pero conserva la conclusion del correo.

5. Entrega el resultado como comunicacion final para el cliente, sin explicar el
   proceso utilizado para producirlo ni mencionar las fuentes de informacion.

### SALIDA

Devuelve unicamente un objeto JSON valido con esta estructura:

{
  "descripcion_correo_cliente": "...",
  "descripcion_plataforma_cliente": "..."
}"""

_REQUIRED_OUTPUT_KEYS = {"descripcion_correo_cliente", "descripcion_plataforma_cliente"}
_SENTENCE_END = re.compile(r"[.!?]+(?=(?:\s|$))")
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)
_CONTEXT_FIELDS = (
    "title",
    "resumen",
    "descripcion_circuito",
    "ubicacion_componente",
    "condiciones_ejecucion",
    "condiciones_establecimiento",
    "accion_codigo_activo",
    "condiciones_borrar",
    "charla_taller",
)


class NavifaultClientDescriptionError(RuntimeError):
    """El proveedor o la salida no permite producir una comunicación segura."""

    def __init__(
        self,
        message: str,
        *,
        raw_output: str | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.raw_output = raw_output
        self.diagnostics = diagnostics or {}


@dataclass(frozen=True)
class ClaimedClientDescription:
    description_id: str
    locked_at: datetime


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _context_sha256(context: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(context).encode("utf-8")).hexdigest()


def _description_id(
    fault_page_id: str, context_sha256: str, prompt_version: str, model_version: str
) -> str:
    identity = "\x1f".join((fault_page_id, context_sha256, prompt_version, model_version))
    return "client_desc_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _generation_status(value: str) -> GenerationStatus:
    if value not in {"pending", "ready", "processing", "failed"}:
        raise RuntimeError(f"Estado de comunicación Navifault inválido: {value!r}")
    return cast(GenerationStatus, value)


def _parse_output(value: str) -> tuple[str, str]:
    candidate = value.strip()
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        match = _JSON_OBJECT.search(candidate)
        if match is None:
            raise NavifaultClientDescriptionError("El modelo no devolvió JSON válido") from exc
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError as nested_exc:
            raise NavifaultClientDescriptionError("El modelo no devolvió JSON válido") from nested_exc
    if not isinstance(payload, dict) or set(payload) != _REQUIRED_OUTPUT_KEYS:
        raise NavifaultClientDescriptionError(
            "El modelo no devolvió exactamente las dos descripciones de cliente"
        )

    correo = payload["descripcion_correo_cliente"]
    plataforma = payload["descripcion_plataforma_cliente"]
    if not isinstance(correo, str) or not isinstance(plataforma, str):
        raise NavifaultClientDescriptionError("Las descripciones del modelo no son texto")
    correo, plataforma = correo.strip(), plataforma.strip()
    if not correo or not plataforma:
        raise NavifaultClientDescriptionError("El modelo devolvió una descripción vacía")

    sentences = len(_SENTENCE_END.findall(correo))
    if sentences not in {3, 4}:
        raise NavifaultClientDescriptionError(
            f"El correo debe tener 3 o 4 oraciones y devolvió {sentences}"
        )
    paragraphs = _split_paragraphs(plataforma)
    if len(paragraphs) != 2:
        raise NavifaultClientDescriptionError(
            f"La plataforma debe tener 2 párrafos y devolvió {len(paragraphs)}"
        )
    return correo, "\n\n".join(paragraphs)


def _split_paragraphs(value: str) -> list[str]:
    """Separa los dos párrafos de plataforma sin exigir una línea en blanco.

    El modelo alterna entre separar con línea en blanco y con un solo salto de
    línea según la corrida; ambas formas son el mismo texto, y exigir solo la
    primera rechazaba salidas correctas.
    """
    blocks = [block.strip() for block in re.split(r"\n\s*\n", value) if block.strip()]
    if len(blocks) == 1:
        blocks = [block.strip() for block in value.split("\n") if block.strip()]
    return blocks


def _page_context(page: NavifaultFaultPage) -> dict[str, Any]:
    source = page.source_record if isinstance(page.source_record, dict) else {}
    codes = source.get("codes_parsed") if isinstance(source.get("codes_parsed"), dict) else {}
    return {
        "idioma": page.language.lower(),
        "pub_id": page.pub_id,
        "codigo_falla": page.fault_code,
        "variante": page.variant,
        "lampara": codes.get("lampara"),
        # `engine_model` NO se entrega al modelo. En 4 de 12 fallas muestreadas
        # no es el motor del vehiculo sino la lista de familias que cubre la
        # publicacion Cummins ("ISF2.8 CM2220 EC; ISF3.8 CM2220; ISL9 CM2150
        # SN; ..."), asi que nombrarla era elegir de un catalogo. El cliente
        # ademas ya sabe que motor tiene: no aporta a la comunicacion.
        "title": page.title,
        "resumen": page.summary,
        **{field: source.get(field) for field in _CONTEXT_FIELDS if source.get(field)},
    }


async def _verified_complementary_page(
    session: AsyncSession, page: NavifaultFaultPage
) -> NavifaultFaultPage | None:
    link = NavifaultFaultPageLanguageLink
    result = await session.scalars(
        select(NavifaultFaultPage)
        .join(
            link,
            or_(
                link.target_fault_page_id == NavifaultFaultPage.fault_page_id,
                link.source_fault_page_id == NavifaultFaultPage.fault_page_id,
            ),
        )
        .where(
            link.status == "verified",
            or_(
                link.source_fault_page_id == page.fault_page_id,
                link.target_fault_page_id == page.fault_page_id,
            ),
            NavifaultFaultPage.fault_page_id != page.fault_page_id,
            NavifaultFaultPage.language != page.language,
        )
        .order_by(NavifaultFaultPage.fault_page_id)
    )
    candidates = list(result.all())
    return candidates[0] if len(candidates) == 1 else None


async def build_client_context(
    session: AsyncSession, resolution: navifault_llm_service.Resolution
) -> dict[str, Any]:
    page = resolution.page
    primary_key = "pagina_espanol" if page.language.lower().startswith("es") else "pagina_ingles_principal"
    context: dict[str, Any] = {primary_key: _page_context(page)}
    complementary = await _verified_complementary_page(session, page)
    if complementary is not None:
        complementary_key = (
            "pagina_ingles_complementaria"
            if complementary.language.lower().startswith("en")
            else "pagina_espanol_complementaria"
        )
        context[complementary_key] = _page_context(complementary)
    return context


async def _call_llm(
    context: dict[str, Any], *, correction: str | None = None
) -> tuple[tuple[str, str], dict[str, Any]]:
    """Pide las dos comunicaciones al proveedor y valida su contrato.

    ``correction`` es el motivo exacto por el que se rechazó un intento
    anterior. Se devuelve al modelo como instrucción, porque decirle qué
    incumplió es más efectivo que repetir el mismo prompt a ciegas.
    """
    if not settings.navifault_llm_enabled or not settings.navifault_llm_base_url:
        raise NavifaultClientDescriptionError("El proveedor LLM de Navifault no está habilitado")
    messages: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "### CONTEXTO COMPLETO DEL CODIGO DE FALLA\n" + _canonical_json(context),
        },
    ]
    if correction:
        messages.append(
            {
                "role": "user",
                "content": (
                    "### CORRECCION\n"
                    f"La respuesta anterior fue rechazada: {correction}\n"
                    "Vuelve a redactarla respetando exactamente las reglas de negocio "
                    "y el formato JSON solicitado. No expliques la correccion."
                ),
            }
        )
    payload: dict[str, Any] = {
        "model": settings.navifault_llm_model,
        "messages": messages,
        "temperature": settings.navifault_llm_temperature,
    }
    # `None` significa sin techo: se omite `max_tokens` y el límite efectivo lo
    # pone el proveedor, acotado por el timeout de la petición.
    if settings.navifault_llm_max_output_tokens is not None:
        payload["max_tokens"] = settings.navifault_llm_max_output_tokens
    if settings.navifault_llm_disable_thinking:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    try:
        async with httpx.AsyncClient(timeout=settings.navifault_llm_timeout_seconds) as client:
            response = await client.post(
                f"{settings.navifault_llm_base_url.rstrip('/')}/v1/chat/completions",
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
    except httpx.TimeoutException as exc:
        # Sin techo de tokens el timeout es el único límite de la generación:
        # nombrarlo evita que un runaway se diagnostique como fallo de red.
        raise NavifaultClientDescriptionError(
            "El modelo superó el tiempo máximo de generación "
            f"({settings.navifault_llm_timeout_seconds:.0f} s)"
        ) from exc
    except httpx.HTTPError as exc:
        raise NavifaultClientDescriptionError(
            f"Solicitud LLM falló: {type(exc).__name__}"
        ) from exc
    try:
        choice = body["choices"][0]
        message = choice["message"]
        raw_output = str(message.get("content") or "")
    except (KeyError, IndexError, TypeError) as exc:
        raise NavifaultClientDescriptionError("Respuesta LLM sin contenido") from exc
    finish_reason = choice.get("finish_reason")
    diagnostics: dict[str, Any] = {
        "usage": body.get("usage"),
        "provider": "openai-compatible",
        "finish_reason": finish_reason,
        "reasoning_chars": len(str(message.get("reasoning_content") or "")),
    }
    if not raw_output:
        # Un modelo con razonamiento gasta el techo pensando y responde con
        # `content` vacío: es agotamiento de presupuesto, no un JSON malformado.
        detail = (
            "El modelo agotó el presupuesto de salida antes de responder"
            if finish_reason == "length"
            else "El modelo devolvió una respuesta vacía"
        )
        raise NavifaultClientDescriptionError(
            detail, raw_output=raw_output, diagnostics=diagnostics
        )
    try:
        descriptions = _parse_output(raw_output)
    except NavifaultClientDescriptionError as exc:
        raise NavifaultClientDescriptionError(
            str(exc), raw_output=raw_output, diagnostics=diagnostics
        ) from exc
    return descriptions, {**diagnostics, "raw_output": raw_output}


async def get_or_enqueue(
    session: AsyncSession,
    resolution: navifault_llm_service.Resolution,
    *,
    priority: int = INTERACTIVE_PRIORITY,
    retry_failed: bool = True,
) -> tuple[GenerationStatus, NavifaultGeneratedClientDescription, bool]:
    """Reutiliza o encola la comunicación de una FC ya resuelta.

    ``retry_failed`` distingue los dos orígenes: una petición interactiva
    reintenta un fallo anterior porque hay alguien esperando, mientras que el
    barrido lo respeta para no reintentar en bucle cada ciclo lo que el
    proveedor ya rechazó.
    """
    context = await build_client_context(session, resolution)
    context_sha256 = _context_sha256(context)
    identity = {
        "fault_page_id": resolution.page.fault_page_id,
        "context_sha256": context_sha256,
        "prompt_version": PROMPT_VERSION,
        "model_version": settings.navifault_llm_model,
    }
    existing = await session.scalar(
        select(NavifaultGeneratedClientDescription)
        .where(
            NavifaultGeneratedClientDescription.fault_page_id == identity["fault_page_id"],
            NavifaultGeneratedClientDescription.context_sha256 == identity["context_sha256"],
            NavifaultGeneratedClientDescription.prompt_version == identity["prompt_version"],
            NavifaultGeneratedClientDescription.model_version == identity["model_version"],
        )
        .with_for_update()
    )
    if existing is not None:
        current_status = _generation_status(existing.status)
        if current_status != "failed":
            if priority > existing.priority and current_status == "pending":
                # Un usuario abrió la ficha de algo que el barrido ya encoló:
                # la misma fila sube de prioridad en vez de duplicarse.
                existing.priority = priority
                await session.commit()
            return current_status, existing, True
        if not retry_failed:
            return current_status, existing, True
        previous_error = (
            existing.generation_metadata.get("error")
            if isinstance(existing.generation_metadata, dict)
            else None
        )
        existing.status = "pending"
        existing.priority = priority
        existing.descripcion_correo_cliente = None
        existing.descripcion_plataforma_cliente = None
        existing.generation_metadata = {
            "context": context,
            "previous_error": previous_error,
            "retried_at": datetime.now(UTC).isoformat(),
        }
        await session.commit()
        return "pending", existing, False

    row = NavifaultGeneratedClientDescription(
        description_id=_description_id(**identity),
        **identity,
        status="pending",
        priority=priority,
        descripcion_correo_cliente=None,
        descripcion_plataforma_cliente=None,
        generation_metadata={"context": context},
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        winner = await session.scalar(
            select(NavifaultGeneratedClientDescription).where(
                NavifaultGeneratedClientDescription.fault_page_id == identity["fault_page_id"],
                NavifaultGeneratedClientDescription.context_sha256 == identity["context_sha256"],
                NavifaultGeneratedClientDescription.prompt_version == identity["prompt_version"],
                NavifaultGeneratedClientDescription.model_version == identity["model_version"],
            )
        )
        if winner is None:
            raise
        return _generation_status(winner.status), winner, True
    return "pending", row, False


async def claim_one(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    lane: Literal["interactive", "background"] | None = None,
) -> ClaimedClientDescription | None:
    """Toma un elemento de la cola y lo marca en proceso.

    ``lane`` reparte la cola en dos mitades excluyentes. El carril interactivo
    sólo reclama lo que pidió un usuario con la ficha abierta, y el de fondo
    sólo lo pregenerado: así el barrido nunca ocupa el slot reservado y un
    slot libre no se queda esperando trabajo del otro carril.
    """
    moment = now or datetime.now(UTC)
    stale_cutoff = moment - timedelta(seconds=settings.navifault_llm_processing_stale_seconds)
    conditions = [
        or_(
            NavifaultGeneratedClientDescription.status == "pending",
            (NavifaultGeneratedClientDescription.status == "processing")
            & (NavifaultGeneratedClientDescription.updated_at <= stale_cutoff),
        )
    ]
    if lane == "interactive":
        conditions.append(
            NavifaultGeneratedClientDescription.priority >= INTERACTIVE_PRIORITY
        )
    elif lane == "background":
        conditions.append(
            NavifaultGeneratedClientDescription.priority < INTERACTIVE_PRIORITY
        )
    row = await session.scalar(
        select(NavifaultGeneratedClientDescription)
        .where(*conditions)
        .order_by(
            NavifaultGeneratedClientDescription.priority.desc(),
            NavifaultGeneratedClientDescription.created_at,
            NavifaultGeneratedClientDescription.description_id,
        )
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if row is None:
        return None
    row.status = "processing"
    row.updated_at = moment
    await session.commit()
    return ClaimedClientDescription(description_id=row.description_id, locked_at=moment)


async def _finish_claim(
    claimed: ClaimedClientDescription,
    *,
    status: Literal["ready", "failed"],
    correo: str | None,
    plataforma: str | None,
    metadata: dict[str, Any],
) -> bool:
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            update(NavifaultGeneratedClientDescription)
            .where(
                NavifaultGeneratedClientDescription.description_id == claimed.description_id,
                NavifaultGeneratedClientDescription.status == "processing",
                NavifaultGeneratedClientDescription.updated_at == claimed.locked_at,
            )
            .values(
                status=status,
                descripcion_correo_cliente=correo,
                descripcion_plataforma_cliente=plataforma,
                generation_metadata=metadata,
            )
            .returning(NavifaultGeneratedClientDescription.description_id)
        )
        await session.commit()
    return result.first() is not None


async def process_claimed(claimed: ClaimedClientDescription) -> bool:
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        row = await session.scalar(
            select(NavifaultGeneratedClientDescription).where(
                NavifaultGeneratedClientDescription.description_id == claimed.description_id,
                NavifaultGeneratedClientDescription.status == "processing",
                NavifaultGeneratedClientDescription.updated_at == claimed.locked_at,
            )
        )
        if row is None:
            return False
        context = row.generation_metadata.get("context")

    if not isinstance(context, dict):
        return await _finish_claim(
            claimed,
            status="failed",
            correo=None,
            plataforma=None,
            metadata={
                "error": "La cola no conserva un contexto de cliente válido",
                "failed_at": datetime.now(UTC).isoformat(),
            },
        )
    retried_after: str | None = None
    try:
        (correo, plataforma), provider_metadata = await _call_llm(context)
    except NavifaultClientDescriptionError as exc:
        if not exc.raw_output:
            # Sin salida no hay nada que corregir: el presupuesto se agotó o el
            # proveedor no respondió, y repetir la llamada daría lo mismo.
            return await _finish_claim(
                claimed,
                status="failed",
                correo=None,
                plataforma=None,
                metadata={
                    "context": context,
                    # `finish_reason` y `usage` distinguen un presupuesto agotado
                    # de una salida que sí llegó pero no cumple el contrato.
                    **exc.diagnostics,
                    "error": str(exc),
                    "raw_output": exc.raw_output,
                    "failed_at": datetime.now(UTC).isoformat(),
                },
            )
        # El modelo redactó, pero incumplió el contrato de salida. Se le indica
        # qué falló y se le da un único segundo intento antes de rendirse.
        retried_after = str(exc)
        try:
            (correo, plataforma), provider_metadata = await _call_llm(
                context, correction=retried_after
            )
        except NavifaultClientDescriptionError as retry_exc:
            return await _finish_claim(
                claimed,
                status="failed",
                correo=None,
                plataforma=None,
                metadata={
                    "context": context,
                    **retry_exc.diagnostics,
                    "error": str(retry_exc),
                    "raw_output": retry_exc.raw_output,
                    "first_attempt_error": retried_after,
                    "first_attempt_raw_output": exc.raw_output,
                    "attempts": 2,
                    "failed_at": datetime.now(UTC).isoformat(),
                },
            )
    except Exception as exc:
        return await _finish_claim(
            claimed,
            status="failed",
            correo=None,
            plataforma=None,
            metadata={
                "context": context,
                "error": f"Error LLM inesperado: {type(exc).__name__}",
                "failed_at": datetime.now(UTC).isoformat(),
            },
        )
    if retried_after is not None:
        provider_metadata = {
            **provider_metadata,
            "retried_after": retried_after,
            "attempts": 2,
        }
    return await _finish_claim(
        claimed,
        status="ready",
        correo=correo,
        plataforma=plataforma,
        metadata={
            "context": context,
            **provider_metadata,
            "completed_at": datetime.now(UTC).isoformat(),
        },
    )


#: Una llave por serie de motor y protocolo: es lo que define la FC del manual,
#: así que dos vehículos del mismo motor con la misma falla comparten
#: generación. Se toma un ``row_id`` representativo por llave.
_RECENT_FAULT_KEYS = text(
    """
    select distinct on (v.service_model_name, f.nombre_fuente_diagnostico,
                        f.codigo_diagnostico, f.codigo_modo_de_falla)
           f.row_id
    from analytics.fact_fault_event f
    join analytics.dim_vehicle d on d.vehicle_id = f.vehicle_id
    join geotab_databases g on lower(g.database_key) = lower(d.database_name)
    join vehicles v on v.geotab_database_id = g.id
                   and v.geotab_device_id = d.device_id
                   and v.plate = f.movil
    where f.fecha >= :desde_fecha
      and f.fecha_de_falla >= :desde
      and v.is_active
      and v.fleet_id is not null
    order by v.service_model_name, f.nombre_fuente_diagnostico,
             f.codigo_diagnostico, f.codigo_modo_de_falla, f.row_id
    """
)


async def sweep_recent_faults(
    session: AsyncSession, *, hours: int, limit: int
) -> tuple[int, int]:
    """Encola las fallas recientes que aún no tienen comunicación generada.

    Devuelve ``(llaves_revisadas, encoladas)``. Una llave sin FC Cummins exacta
    o con varias candidatas se omite en silencio: resolver una variante por
    intuición es justamente lo que el módulo no debe hacer.
    """
    desde = datetime.now(UTC) - timedelta(hours=hours)
    rows = await session.execute(
        _RECENT_FAULT_KEYS, {"desde": desde, "desde_fecha": desde.date()}
    )
    row_ids = [row[0] for row in rows.all()]
    enqueued = 0
    for row_id in row_ids:
        if enqueued >= limit:
            break
        try:
            context = await navifault_llm_service.get_analytics_fault_context(session, row_id)
            resolution = await navifault_llm_service.resolve_fault_page(
                session, navifault_llm_service.fault_input_from_analytics(context)
            )
        except navifault_llm_service.NavifaultResolutionError:
            continue
        status, _row, cached = await get_or_enqueue(
            session, resolution, priority=SWEEP_PRIORITY, retry_failed=False
        )
        if status == "pending" and not cached:
            enqueued += 1
    return len(row_ids), enqueued


async def sweep_once() -> tuple[int, int]:
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        return await sweep_recent_faults(
            session,
            hours=settings.navifault_llm_sweep_hours,
            limit=settings.navifault_llm_sweep_max_enqueued,
        )


async def _drain_lane(
    *, lane: Literal["interactive", "background"], slots: int
) -> int:
    """Consume un carril con ``slots`` generaciones simultáneas.

    ``claim_one`` usa ``skip_locked``, así que varios slots reclaman a la vez
    sin tomar la misma fila.
    """
    from app.db.session import AsyncSessionLocal

    async def _slot() -> int:
        # Acotado por pasada: ``run_once`` tiene que devolver el control para
        # que el worker alcance a barrer y a atender señales de apagado.
        done = 0
        for _ in range(settings.navifault_llm_worker_batch_size):
            async with AsyncSessionLocal() as session:
                claimed = await claim_one(session, lane=lane)
            if claimed is None:
                break
            await process_claimed(claimed)
            done += 1
        return done

    return sum(await asyncio.gather(*(_slot() for _ in range(max(slots, 1)))))


async def run_lane_once(lane: Literal["interactive", "background"]) -> int:
    """Procesa una pasada de un solo carril.

    El worker corre un bucle independiente por carril. Sincronizarlos en cada
    pasada anularía el propósito: si el carril interactivo queda vacío y espera
    al de fondo, la ficha recién abierta vuelve a esperar la generación en
    curso, que es justo lo que los dos carriles evitan.
    """
    slots = (
        settings.navifault_llm_interactive_slots
        if lane == "interactive"
        else settings.navifault_llm_background_slots
    )
    return await _drain_lane(lane=lane, slots=slots)


async def run_once() -> int:
    """Una pasada de ambos carriles. La usan ``--once`` y las pruebas."""
    interactive, background = await asyncio.gather(
        run_lane_once("interactive"),
        run_lane_once("background"),
    )
    return interactive + background
