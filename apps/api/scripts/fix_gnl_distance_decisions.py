"""Resuelve las anomalías de distancia de una flota con la fuente que el combustible respalda.

POR QUÉ EXISTE
--------------
El ETL marca un día cuando ECM y GPS discrepan más allá del umbral, y para
`ecm_gps_warning_mismatch` / `ecm_gps_critical_mismatch` su resolución
automática es SIEMPRE `exclude`: nunca cae a ECM. El día queda con
`kms_effective` NULL y desaparece de los reportes.

Medido en la flota GNL sobre 1.303 vehículo-días: 145 días (11,1 %) sin
distancia efectiva, o sea 31.802 km que no se publican, el 11,5 % del total.

Cuál fuente está mal no lo decide la velocidad —en 78 de 88 días marcados AMBAS
dan velocidades plausibles y solo discrepan en el total del día—, así que se usa
el COMBUSTIBLE como tercera fuente independiente: si el kilometraje de una
fuente produce un rendimiento (km por unidad de combustible) parecido al normal
de ese vehículo, esa es la fuente buena. El resultado en GNL fue ECM 101, GPS
16, empate 18.

QUÉ NO ES
---------
No es un arreglo de la causa. El GPS seguirá perdiendo viajes mientras la
atribución por día del ETL no se corrija (los viajes se agrupan por `stop - 5h`
y las lecturas de ECM por otro criterio, de ahí el patrón de días consecutivos
donde lo que falta un día sobra al siguiente). Esto publica el dato correcto
para los días ya marcados; no evita que aparezcan nuevos.

GARANTÍAS
---------
- Dry-run por defecto. Escribe solo con `--apply`.
- Idempotente: `dedupe_key` incluye la huella de la observación, así que
  re-ejecutar no duplica. Si la huella cambia porque el ETL recalculó el día,
  se emite una decisión nueva, que es justo lo que debe pasar.
- Reversible: todas las filas que escribe llevan el mismo prefijo de
  `dedupe_key` y se pueden borrar con un solo DELETE.
- No toca `analytics`. Solo inserta en `public.distance_quality_decisions`, que
  es append-only y es el mecanismo previsto para esto.
- Nunca elige una fuente sin dato: si la fuente ganadora es NULL o 0, el día se
  salta y queda como estaba.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter

from sqlalchemy import select, text

sys.path.insert(0, "/app")

from app.db.session import AsyncSessionLocal
from app.models.distance_quality import DistanceQualityDecision
from app.models.fleet import Fleet
from app.models.user import User
from app.services import analytics_service as svc

#: Margen en desviaciones estándar por el que una fuente tiene que ganar para
#: que se la prefiera. Por debajo se considera empate y manda el ECM, que es la
#: fuente primaria del hecho en el 87 % de los días.
MARGEN_SD = 0.25

PREFIJO_DEDUPE = "dist-fix-combustible"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fleet", default="GNL", help="nombre de la flota")
    ap.add_argument(
        "--actor",
        required=True,
        help="email de la cuenta bajo la que queda registrada la corrección",
    )
    ap.add_argument("--apply", action="store_true", help="escribe; sin esto es dry-run")
    args = ap.parse_args()

    async with AsyncSessionLocal() as db:
        fleet = (
            await db.execute(select(Fleet).where(Fleet.name == args.fleet))
        ).scalar_one_or_none()
        if fleet is None:
            print(f"Flota {args.fleet!r} no encontrada")
            return 1
        actor = (
            await db.execute(select(User).where(User.email == args.actor))
        ).scalar_one_or_none()
        if actor is None:
            print(f"Usuario {args.actor!r} no encontrado")
            return 1

        ids = list(await svc.fleet_analytics_vehicle_ids(db, [fleet.id]) or [])
        if not ids:
            print("La flota no resuelve ningún vehículo de analytics")
            return 1

        # Línea base de rendimiento por vehículo, tomada SOLO de días sanos.
        base = {
            p: (float(mu), float(sd or 0))
            for p, mu, sd in (
                await db.execute(
                    text("""
                    select placa,
                           avg(kms_effective / nullif(comb, 0)),
                           stddev(kms_effective / nullif(comb, 0))
                    from analytics.fact_combustible_daily
                    where vehicle_id = any(:ids)
                      and distance_quality_reason = 'ok'
                      and comb > 0 and kms_effective > 0
                    group by 1"""),
                    {"ids": ids},
                )
            ).all()
        }

        filas = (
            await db.execute(
                text("""
                select fact_row_id, vehicle_id, date_key, placa, fecha,
                       kms_ecm, kms_gps, comb, distance_quality_reason,
                       distance_quality_fingerprint, distance_threshold_version
                from analytics.fact_combustible_daily
                where vehicle_id = any(:ids) and kms_effective is null
                order by placa, fecha"""),
                {"ids": ids},
            )
        ).mappings().all()

        ya = {
            r[0]
            for r in (
                await db.execute(
                    text("""select dedupe_key from public.distance_quality_decisions
                            where dedupe_key like :p"""),
                    {"p": f"{PREFIJO_DEDUPE}:%"},
                )
            ).all()
        }

        conteo: Counter[str] = Counter()
        nuevas: list[DistanceQualityDecision] = []
        km_total = 0.0

        for f in filas:
            ke = float(f["kms_ecm"] or 0)
            kg = float(f["kms_gps"] or 0)
            m3 = float(f["comb"] or 0)
            if ke <= 0 and kg <= 0:
                conteo["sin dato en ninguna fuente (se salta)"] += 1
                continue

            accion, valor, motivo = "use_ecm", ke, "ecm_por_defecto"
            mu_sd = base.get(f["placa"])
            if m3 > 0 and mu_sd:
                mu, sd = mu_sd
                sd = sd if sd > 0.01 else mu * 0.15
                d_ecm = abs(ke / m3 - mu) / sd if ke > 0 else float("inf")
                d_gps = abs(kg / m3 - mu) / sd if kg > 0 else float("inf")
                if d_gps < d_ecm - MARGEN_SD:
                    accion, valor, motivo = "use_gps", kg, "rendimiento_respalda_gps"
                elif d_ecm < d_gps - MARGEN_SD:
                    motivo = "rendimiento_respalda_ecm"
            if valor <= 0:
                conteo["la fuente ganadora no tiene dato (se salta)"] += 1
                continue

            clave = f"{PREFIJO_DEDUPE}:{f['fact_row_id']}:{f['distance_quality_fingerprint']}"
            if clave in ya:
                conteo["ya resuelta antes (idempotencia)"] += 1
                continue

            conteo[accion] += 1
            km_total += valor
            nuevas.append(
                DistanceQualityDecision(
                    fact_row_id=f["fact_row_id"],
                    analytics_vehicle_id=f["vehicle_id"] or "",
                    date_key=int(f["date_key"] or 0),
                    observation_fingerprint=f["distance_quality_fingerprint"],
                    origin="manual",
                    action=accion,
                    reason_code=motivo,
                    justification=(
                        f"Anomalía {f['distance_quality_reason']}. Se publica "
                        f"{'ECM' if accion == 'use_ecm' else 'GPS'}: su rendimiento "
                        f"({(valor / m3):.2f} por unidad de combustible) es el que se "
                        f"parece al normal de este vehículo. Corrección por regla "
                        f"aplicada en lote con scripts/fix_gnl_distance_decisions.py; "
                        f"el criterio y su evidencia están en el docstring del script."
                    ) if m3 > 0 else (
                        f"Anomalía {f['distance_quality_reason']}. Se publica "
                        f"{'ECM' if accion == 'use_ecm' else 'GPS'} por ser la fuente "
                        f"primaria del hecho; el día no tiene combustible para "
                        f"contrastar. Corrección por regla aplicada en lote con "
                        f"scripts/fix_gnl_distance_decisions.py."
                    ),
                    observed_kms_ecm=f["kms_ecm"],
                    observed_kms_gps=f["kms_gps"],
                    threshold_version=f["distance_threshold_version"],
                    actor_user_id=actor.id,
                    actor_email=actor.email,
                    dedupe_key=clave,
                )
            )

        print(f"Flota {args.fleet}: {len(filas)} días sin distancia efectiva")
        for cat, n in sorted(conteo.items(), key=lambda kv: -kv[1]):
            print(f"  {cat:44} {n:>5}")
        print(f"  {'km que se recuperan':44} {km_total:>9,.0f}")

        if not args.apply:
            print("\nDRY-RUN: no se escribió nada. Repite con --apply.")
            return 0

        db.add_all(nuevas)
        await db.commit()
        print(f"\nAplicado: {len(nuevas)} decisiones escritas.")
        print(f"Para revertir: DELETE FROM public.distance_quality_decisions "
              f"WHERE dedupe_key LIKE '{PREFIJO_DEDUPE}:%';")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
