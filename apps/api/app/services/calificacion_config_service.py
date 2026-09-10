"""Lectura y escritura de la calibración de la calificación por flota.

La tabla es append-only (`calificacion_config_versions`): la última fila por
flota gana. Este servicio esconde ese detalle y entrega una
`EffectiveCalificacionConfig`, que es lo único que el resto del código necesita
saber: qué calibración aplica, de dónde salió y quién la dejó así.

**La regla multi-flota es el punto delicado.** Los reportes reciben un alcance
que puede traer varias flotas a la vez. Promediar dos fórmulas distintas en un
mismo puntaje produce un número que no significa nada, así que cuando las
calibraciones del alcance difieren se devuelven los valores por defecto y
`origen="mixto"`: la interfaz puede entonces pedir al usuario que elija una
flota. NO se promedia ni se elige una arbitrariamente.

Robustez deliberada: una fila almacenada que resulte inválida al leerse NO
tumba la pantalla de reportes. Se registra un `warning` con el `fleet_id` y se
cae a los valores por defecto. Una calibración corrupta es un problema de esa
flota, no del reporte completo.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.calificacion_config import CalificacionConfigVersion
from app.services.calificacion_config import (
    DEFAULT_CALIFICACION_CONFIG,
    EDITABLE_FIELDS,
    PENALIZACION_CODES,
    CalificacionConfig,
    CalificacionConfigError,
    config_from_mapping,
    config_to_mapping,
    validate_config,
)

log = logging.getLogger(__name__)

# Origen de la calibración efectiva.
ORIGEN_DEFECTO = "defecto"
ORIGEN_FLOTA = "flota"
ORIGEN_MIXTO = "mixto"

MAX_HISTORY_LIMIT = 100


@dataclass(frozen=True)
class EffectiveCalificacionConfig:
    """La calibración que aplica, más su procedencia.

    `origen`:
    - `"defecto"`: los valores del código. Es también lo que se devuelve cuando
      una flota hizo "restablecer" — en ese caso `actualizado_en`/`_por` sí
      vienen poblados, porque alguien tomó esa decisión y debe constar.
    - `"flota"`: la calibración guardada por la flota.
    - `"mixto"`: el alcance mezcla flotas con calibraciones distintas. El
      `config` son los defaults y NO debe presentarse como el de nadie.
    """

    config: CalificacionConfig
    origen: str
    fleet_id: uuid.UUID | None
    actualizado_en: datetime | None
    actualizado_por: str | None


@dataclass(frozen=True)
class _ResolvedRow:
    """Resolución de la última fila de UNA flota."""

    config: CalificacionConfig
    # True sólo si la calibración salió de una fila válida y no-reset.
    es_flota: bool
    actualizado_en: datetime | None
    actualizado_por: str | None


_DEFAULT_RESOLUTION = _ResolvedRow(
    config=DEFAULT_CALIFICACION_CONFIG,
    es_flota=False,
    actualizado_en=None,
    actualizado_por=None,
)


def _row_to_mapping(row: CalificacionConfigVersion) -> dict[str, Any]:
    """Extrae sólo los campos editables de la fila.

    Las columnas en NULL se omiten para que `config_from_mapping` caiga al
    default: es lo que hace legible una fila escrita antes de añadir un campo.
    """
    valores: dict[str, Any] = {}
    for nombre in EDITABLE_FIELDS:
        valor = getattr(row, nombre, None)
        if valor is not None:
            valores[nombre] = valor
    return valores


def _resolve_row(row: CalificacionConfigVersion) -> _ResolvedRow:
    if row.is_reset:
        # La flota volvió a los defaults, pero la decisión tiene autor y fecha.
        return _ResolvedRow(
            config=DEFAULT_CALIFICACION_CONFIG,
            es_flota=False,
            actualizado_en=row.created_at,
            actualizado_por=row.actor_email,
        )
    valores = _row_to_mapping(row)
    # Al LEER se descarta un código de penalización que el registro ya no
    # conoce; al ESCRIBIR se sigue rechazando. La asimetría es deliberada: un
    # cliente no debe inventar códigos, pero una clave huérfana en una fila
    # guardada no puede costar la calibración ENTERA de la flota. Sin esto,
    # retirar una penalización del registro haría que cada flota que la tuviera
    # anotada perdiera en silencio sus pesos, umbrales y topes, y su puntaje
    # cambiara sin que nadie lo decidiera.
    guardadas = valores.get("penalizaciones")
    if isinstance(guardadas, Mapping):
        vigentes = {k: v for k, v in guardadas.items() if k in PENALIZACION_CODES}
        if len(vigentes) != len(guardadas):
            log.warning(
                "Calibración con penalizaciones que ya no existen; se ignoran y el "
                "resto de la calibración se conserva. fleet_id=%s version_id=%s "
                "desconocidas=%s",
                row.fleet_id,
                row.id,
                sorted(set(guardadas) - set(vigentes)),
            )
            valores["penalizaciones"] = vigentes
    try:
        config = config_from_mapping(valores)
    except CalificacionConfigError as exc:
        # Fail-soft a propósito: el reporte se sigue pudiendo ver.
        log.warning(
            "Calibración de calificación inválida en base; se usan los valores por "
            "defecto. fleet_id=%s version_id=%s motivo=%s",
            row.fleet_id,
            row.id,
            exc,
        )
        return _DEFAULT_RESOLUTION
    return _ResolvedRow(
        config=config,
        es_flota=True,
        actualizado_en=row.created_at,
        actualizado_por=row.actor_email,
    )


async def _latest_rows(
    db: AsyncSession, fleet_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, CalificacionConfigVersion]:
    """Última fila de cada flota en UNA sola consulta.

    `DISTINCT ON (fleet_id) ... ORDER BY fleet_id, created_at DESC` es la forma
    natural en PostgreSQL y la que aprovecha
    `ix_calificacion_config_fleet_created`. Un round-trip por flota convertiría
    un alcance de 20 flotas en 20 consultas dentro de cada petición de reportes.
    """
    if not fleet_ids:
        return {}
    m = CalificacionConfigVersion
    stmt = (
        select(m)
        .where(m.fleet_id.in_(list(fleet_ids)))
        .distinct(m.fleet_id)
        .order_by(m.fleet_id, m.created_at.desc(), m.id.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return {row.fleet_id: row for row in rows}


def _comparable(config: CalificacionConfig) -> dict[str, Any]:
    """La calibración en la forma en la que dos flotas se pueden comparar.

    `config_to_mapping` serializa el mapa de penalizaciones **tal como se
    guardó**, y ahí una clave ausente significa "el default del registro". Dos
    flotas con `{}` y con `{"sobrevelocidad_rpm": true}` calculan exactamente el
    mismo puntaje, así que compararlas literalmente las declararía distintas y
    devolvería `origen="mixto"` —defaults y un aviso de "elige una flota"— sin
    que hubiera nada mezclado.

    Por eso el mapa se resuelve al valor EFECTIVO de cada código conocido antes
    de comparar, con la misma `penalizacion_activa` que usa el cálculo. Un
    código que ya no está en el registro no participa: tampoco participa en el
    puntaje.
    """
    valores = config_to_mapping(config)
    valores["penalizaciones"] = {
        code: config.penalizacion_activa(code) for code in sorted(PENALIZACION_CODES)
    }
    return valores


def _same_config(a: CalificacionConfig, b: CalificacionConfig) -> bool:
    """¿Producen estas dos calibraciones el mismo puntaje?

    Es la pregunta que decide `origen="mixto"`, y por eso se compara el efecto y
    no la fila: incluye el mapa de penalizaciones normalizado.
    """
    return _comparable(a) == _comparable(b)


async def effective_config(
    db: AsyncSession, fleet_ids: Sequence[uuid.UUID] | None
) -> EffectiveCalificacionConfig:
    """Calibración que aplica a un alcance de 0..N flotas.

    - alcance vacío o `None` → defaults, `origen="defecto"`;
    - una flota → su calibración si la tiene;
    - varias flotas con la MISMA calibración efectiva → esa calibración;
    - varias flotas con calibraciones distintas → defaults, `origen="mixto"`.
    """
    ids = list(dict.fromkeys(fleet_ids or ()))
    if not ids:
        return EffectiveCalificacionConfig(
            config=DEFAULT_CALIFICACION_CONFIG,
            origen=ORIGEN_DEFECTO,
            fleet_id=None,
            actualizado_en=None,
            actualizado_por=None,
        )

    filas = await _latest_rows(db, ids)
    resoluciones: list[tuple[uuid.UUID, _ResolvedRow]] = [
        (fleet_id, _resolve_row(filas[fleet_id]) if fleet_id in filas else _DEFAULT_RESOLUTION)
        for fleet_id in ids
    ]

    if len(resoluciones) == 1:
        fleet_id, resuelta = resoluciones[0]
        return EffectiveCalificacionConfig(
            config=resuelta.config,
            origen=ORIGEN_FLOTA if resuelta.es_flota else ORIGEN_DEFECTO,
            fleet_id=fleet_id if resuelta.es_flota else None,
            actualizado_en=resuelta.actualizado_en,
            actualizado_por=resuelta.actualizado_por,
        )

    primera = resoluciones[0][1].config
    if not all(_same_config(primera, resuelta.config) for _, resuelta in resoluciones[1:]):
        # Mezclar fórmulas distintas en un promedio no produce un puntaje
        # interpretable. Se devuelve el default y se dice que es mixto.
        return EffectiveCalificacionConfig(
            config=DEFAULT_CALIFICACION_CONFIG,
            origen=ORIGEN_MIXTO,
            fleet_id=None,
            actualizado_en=None,
            actualizado_por=None,
        )

    con_calibracion = [(fid, r) for fid, r in resoluciones if r.es_flota]
    if not con_calibracion:
        return EffectiveCalificacionConfig(
            config=primera,
            origen=ORIGEN_DEFECTO,
            fleet_id=None,
            actualizado_en=None,
            actualizado_por=None,
        )
    # Todas coinciden y al menos una es calibración propia. El `fleet_id` sólo
    # se puede atribuir si hay exactamente una fuente; la fecha y el actor, los
    # de la más reciente de las que aportaron.
    actualizado_en: datetime | None = None
    actualizado_por: str | None = None
    for _, resuelta in con_calibracion:
        fecha = resuelta.actualizado_en
        if fecha is not None and (actualizado_en is None or fecha > actualizado_en):
            actualizado_en = fecha
            actualizado_por = resuelta.actualizado_por
    return EffectiveCalificacionConfig(
        config=primera,
        origen=ORIGEN_FLOTA,
        fleet_id=con_calibracion[0][0] if len(con_calibracion) == 1 else None,
        actualizado_en=actualizado_en,
        actualizado_por=actualizado_por,
    )


async def get_fleet_config(db: AsyncSession, fleet_id: uuid.UUID) -> EffectiveCalificacionConfig:
    """Calibración efectiva de UNA flota, para la pantalla de ajustes."""
    return await effective_config(db, [fleet_id])


async def save_fleet_config(
    db: AsyncSession,
    *,
    fleet_id: uuid.UUID,
    config: CalificacionConfig,
    actor_user_id: uuid.UUID | None,
    actor_email: str | None,
) -> EffectiveCalificacionConfig:
    """Añade una versión de calibración para la flota.

    Valida antes de insertar: la fila que llega a la base tiene que ser legible
    después. Hace `flush` y NO `commit`: el router es el dueño de la
    transacción y debe confirmarla —si no lo hace, `get_db` cierra la sesión sin
    guardar—.
    """
    validate_config(config)
    valores = config_to_mapping(config)
    row = CalificacionConfigVersion(
        fleet_id=fleet_id,
        is_reset=False,
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        **valores,
    )
    db.add(row)
    await db.flush()
    return EffectiveCalificacionConfig(
        config=config,
        origen=ORIGEN_FLOTA,
        fleet_id=fleet_id,
        actualizado_en=row.created_at,
        actualizado_por=row.actor_email,
    )


async def reset_fleet_config(
    db: AsyncSession,
    *,
    fleet_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    actor_email: str | None,
) -> EffectiveCalificacionConfig:
    """Registra que la flota vuelve a los valores por defecto.

    Es una fila más, con los parámetros en NULL y `is_reset=True`, no un borrado
    de las anteriores: el historial de quién calibró qué se conserva completo.
    Igual que `save_fleet_config`, hace `flush` y NO `commit`.
    """
    row = CalificacionConfigVersion(
        fleet_id=fleet_id,
        is_reset=True,
        actor_user_id=actor_user_id,
        actor_email=actor_email,
    )
    db.add(row)
    await db.flush()
    return EffectiveCalificacionConfig(
        config=DEFAULT_CALIFICACION_CONFIG,
        origen=ORIGEN_DEFECTO,
        fleet_id=None,
        actualizado_en=row.created_at,
        actualizado_por=row.actor_email,
    )


async def config_history(
    db: AsyncSession, *, fleet_id: uuid.UUID, limit: int = 20
) -> list[dict[str, Any]]:
    """Versiones de la calibración de una flota, la más reciente primero.

    `valida=False` marca una fila que hoy no se puede aplicar: se muestra para
    que se vea qué pasó, no para reutilizarla.
    """
    acotado = max(1, min(limit, MAX_HISTORY_LIMIT))
    m = CalificacionConfigVersion
    stmt = (
        select(m)
        .where(m.fleet_id == fleet_id)
        .order_by(m.created_at.desc(), m.id.desc())
        .limit(acotado)
    )
    rows = (await db.execute(stmt)).scalars().all()

    historial: list[dict[str, Any]] = []
    for row in rows:
        if row.is_reset:
            config_dict: dict[str, Any] | None = None
            valida = True
        else:
            try:
                config_dict = config_to_mapping(config_from_mapping(_row_to_mapping(row)))
                valida = True
            except CalificacionConfigError:
                config_dict = None
                valida = False
        historial.append(
            {
                "id": str(row.id),
                "creado_en": row.created_at,
                "es_reset": row.is_reset,
                "actor_user_id": str(row.actor_user_id) if row.actor_user_id else None,
                "actor_email": row.actor_email,
                "config": config_dict,
                "valida": valida,
            }
        )
    return historial
