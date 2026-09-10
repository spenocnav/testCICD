"""Mapa operativo dateplate/service_model_name → manual Cummins de Navifault.

El dateplate es la llave de runtime. Los ESN y CPL importados con el corpus se
usan únicamente como evidencia para incorporar dateplates nuevos después de
cada sync maestro. Ningún empate se convierte en una selección silenciosa.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import Table, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.master_data import Vehicle
from app.models.navifault import (
    NavifaultDateplateManualMap,
    NavifaultEngineManualCandidate,
)

AUTOMATIC_ORIGIN = "automatic_esn_observation_v1"
SAMPLES_PER_FIELD = 12


@dataclass(frozen=True)
class DateplateMapRefresh:
    """Auditoría de una evaluación o actualización del mapa automático."""

    audit: dict[str, int]
    rows: tuple[dict[str, Any], ...]
    automatic_rows_retired: int


def normalize_service_model_name(value: str | None) -> str:
    """Normaliza formato, no semántica, para evitar empates falsos."""
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", without_accents).strip().upper()


def _sample(values: Iterable[str]) -> list[str]:
    return sorted({value for value in values if value})[:SAMPLES_PER_FIELD]


def _is_automatic(evidence: object) -> bool:
    return isinstance(evidence, dict) and evidence.get("origin") == AUTOMATIC_ORIGIN


async def _load_observations(
    session: AsyncSession,
    *,
    include_inactive: bool,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    vehicle_query = select(
        Vehicle.plate,
        Vehicle.engine_number,
        Vehicle.service_model_name,
        Vehicle.cpl,
    ).where(
        Vehicle.engine_number.is_not(None),
        Vehicle.service_model_name.is_not(None),
    )
    if not include_inactive:
        vehicle_query = vehicle_query.where(Vehicle.is_active.is_(True))

    candidate_query = select(
        NavifaultEngineManualCandidate.engine_serial,
        NavifaultEngineManualCandidate.pub_id,
        NavifaultEngineManualCandidate.cpl,
        NavifaultEngineManualCandidate.candidate_type,
    )
    vehicles = [dict(row) for row in (await session.execute(vehicle_query)).mappings()]
    candidates_by_engine: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in (await session.execute(candidate_query)).mappings():
        candidates_by_engine[str(row["engine_serial"]).strip()].append(dict(row))
    return vehicles, candidates_by_engine


def _automatic_rows(
    vehicles: list[dict[str, Any]],
    candidates_by_engine: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], Counter[str]]:
    observations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    audit: Counter[str] = Counter()
    for vehicle in vehicles:
        raw_dateplate = str(vehicle["service_model_name"] or "").strip()
        engine = str(vehicle["engine_number"] or "").strip()
        normalized = normalize_service_model_name(raw_dateplate)
        if not normalized or not engine:
            continue
        evidence = candidates_by_engine.get(engine, [])
        primary = [row for row in evidence if row["candidate_type"] == "primary"]
        selected = primary or evidence
        if not selected:
            audit["vehicles_without_cummins_evidence"] += 1
            continue
        audit["vehicles_with_cummins_evidence"] += 1
        observations[normalized].append(
            {
                "plate": str(vehicle["plate"]),
                "engine": engine,
                "cpl": str(vehicle["cpl"] or "").strip(),
                "raw_dateplate": raw_dateplate,
                "pub_ids": sorted({str(row["pub_id"]) for row in selected}),
            }
        )

    created_at = datetime.now(UTC).isoformat()
    rows: list[dict[str, Any]] = []
    for normalized, items in sorted(observations.items()):
        pub_counts: Counter[str] = Counter(pub_id for item in items for pub_id in item["pub_ids"])
        status = "verified" if len(pub_counts) == 1 else "pending"
        for priority, (pub_id, evidence_count) in enumerate(
            sorted(pub_counts.items(), key=lambda pair: (-pair[1], pair[0])),
            start=1,
        ):
            supporting = [item for item in items if pub_id in item["pub_ids"]]
            rows.append(
                {
                    "normalized_service_model_name": normalized,
                    "pub_id": pub_id,
                    "priority": priority,
                    "mapping_status": status,
                    "mapping_evidence": {
                        "origin": AUTOMATIC_ORIGIN,
                        "generated_at": created_at,
                        "raw_dateplate_examples": _sample(item["raw_dateplate"] for item in items),
                        "candidate_pub_ids": sorted(pub_counts),
                        "candidate_vehicle_counts": dict(sorted(pub_counts.items())),
                        "supporting_vehicle_count": len(supporting),
                        "supporting_engine_samples": _sample(item["engine"] for item in supporting),
                        "supporting_plate_samples": _sample(item["plate"] for item in supporting),
                        "supporting_cpl_samples": _sample(item["cpl"] for item in supporting),
                        "evidence_count": evidence_count,
                    },
                }
            )
        audit[f"dateplates_{status}"] += 1
    audit["dateplates_observed"] = len(observations)
    return rows, audit


async def refresh_automatic_dateplate_mappings(
    session: AsyncSession,
    *,
    execute: bool,
    include_inactive: bool = False,
) -> DateplateMapRefresh:
    """Evalúa o actualiza mapas automáticos dentro de la transacción dada.

    Una fila cuya evidencia no es automática se considera una decisión humana:
    domina por dateplate completo y retira cualquier fila automática anterior.
    Las filas automáticas que dejan de tener evidencia se retiran en vez de
    borrarse, preservando trazabilidad y evitando una resolución obsoleta.
    """
    vehicles, candidates_by_engine = await _load_observations(
        session, include_inactive=include_inactive
    )
    rows, audit = _automatic_rows(vehicles, candidates_by_engine)
    audit["vehicles_considered"] = len(vehicles)
    observed_dateplates = {str(row["normalized_service_model_name"]) for row in rows}

    existing_rows: list[NavifaultDateplateManualMap] = []
    if observed_dateplates:
        existing_rows = list(
            (
                await session.scalars(
                    select(NavifaultDateplateManualMap).where(
                        NavifaultDateplateManualMap.normalized_service_model_name.in_(
                            sorted(observed_dateplates)
                        )
                    )
                )
            ).all()
        )

    manual_dateplates = {
        row.normalized_service_model_name
        for row in existing_rows
        if not _is_automatic(row.mapping_evidence)
    }
    candidate_pairs = {
        (str(row["normalized_service_model_name"]), str(row["pub_id"])) for row in rows
    }
    rows_to_upsert = [
        row
        for row in rows
        if str(row["normalized_service_model_name"]) not in manual_dateplates
    ]
    automatic_rows_retired = sum(
        1
        for row in existing_rows
        if _is_automatic(row.mapping_evidence)
        and (
            row.normalized_service_model_name in manual_dateplates
            or (row.normalized_service_model_name, row.pub_id) not in candidate_pairs
        )
    )
    audit["automatic_rows_to_upsert"] = len(rows_to_upsert)
    audit["manual_dateplates_preserved"] = len(manual_dateplates)
    audit["automatic_rows_to_retire"] = automatic_rows_retired

    if not execute:
        return DateplateMapRefresh(
            audit=dict(sorted(audit.items())),
            rows=tuple(rows_to_upsert),
            automatic_rows_retired=automatic_rows_retired,
        )

    retired_at = datetime.now(UTC).isoformat()
    for row in existing_rows:
        if not _is_automatic(row.mapping_evidence):
            continue
        should_retire = (
            row.normalized_service_model_name in manual_dateplates
            or (row.normalized_service_model_name, row.pub_id) not in candidate_pairs
        )
        if should_retire:
            row.mapping_status = "retired"
            row.mapping_evidence = {
                **row.mapping_evidence,
                "retired_at": retired_at,
                "retired_reason": "manual_override"
                if row.normalized_service_model_name in manual_dateplates
                else "no_longer_observed",
            }

    if rows_to_upsert:
        table = cast(Table, NavifaultDateplateManualMap.__table__)
        statement = insert(table).values(rows_to_upsert)
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=["normalized_service_model_name", "pub_id"],
                set_={
                    "priority": statement.excluded.priority,
                    "mapping_status": statement.excluded.mapping_status,
                    "mapping_evidence": statement.excluded.mapping_evidence,
                },
            )
        )
    return DateplateMapRefresh(
        audit=dict(sorted(audit.items())),
        rows=tuple(rows_to_upsert),
        automatic_rows_retired=automatic_rows_retired,
    )
