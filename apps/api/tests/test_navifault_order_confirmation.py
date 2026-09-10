"""Gestionar es confirmar lo que el taller registró, no escribirlo.

El vínculo entre una falla y el trabajo que la resolvió no existe en la API de
CloudFleet, así que lo sustituye la referencia que el taller copia en la
descripción del trabajo. Estas pruebas fijan qué se reconoce como trabajo de la
falla y qué no.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from app.services import navifault_order_service as orden
from app.services.navifault_management_service import (
    ManagedFaultSignature,
    navifault_reference,
)

_VEHICULO = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
_FIRMA = ManagedFaultSignature(
    source="SourceJ1939Id",
    diagnostic_code=1347,
    failure_mode=3.0,
    stop_amber=True,
    stop_red=False,
    malfunction=True,
    warning=False,
)


def _ref(ciclo: int = 1) -> str:
    return navifault_reference(vehicle_id=_VEHICULO, signature=_FIRMA, cycle_number=ciclo)


def _orden(
    labors: list[dict[str, Any]],
    parts: list[dict[str, Any]] | None = None,
    status: str | None = "closed",
):
    async def _fake(number: int, **kwargs: Any) -> dict[str, Any]:
        return {
            "number": number,
            "status": status,
            "labors": labors,
            "parts": parts or [],
        }

    return _fake


def _labor(comment: str, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": 15089,
        "name": "D/M CARCASA DE ENGRANES",
        "code": "01-177-ISM",
        "system": {"name": "MOTOR"},
        "subsystem": None,
        "maintenanceType": {"name": "Correctivo"},
        "comment": comment,
        "createdAt": "2026-09-05T18:13:19.2470000Z",
    }
    base.update(extra)
    return base


async def _buscar(monkeypatch: pytest.MonkeyPatch, fake, ciclo: int = 1):
    monkeypatch.setattr(orden.cloudfleet_service, "get_work_order", fake)
    return await orden.find_fault_work(
        vehicle_id=_VEHICULO, signature=_FIRMA, cycle_number=ciclo, work_order_number=5733
    )


@pytest.mark.asyncio
async def test_el_trabajo_con_la_referencia_es_de_la_falla(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CloudFleet elimina los saltos de línea al copiar el comentario, así que
    la referencia se busca como subcadena y no como línea completa."""
    encontrado = await _buscar(
        monkeypatch,
        _orden([_labor(f"Prueba— Escalada desde Navifault — Ref. Navifault: {_ref()}")]),
    )

    assert encontrado.has_work
    assert len(encontrado.labors) == 1
    assert encontrado.labors[0].system == "MOTOR"
    assert encontrado.labors[0].maintenance_type == "Correctivo"


@pytest.mark.asyncio
async def test_un_trabajo_sin_la_referencia_no_es_de_la_falla(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Una orden cubre varios trabajos y sólo los marcados son de esta falla."""
    encontrado = await _buscar(
        monkeypatch, _orden([_labor("Cambio de aceite programado"), _labor("Revisión general")])
    )

    assert not encontrado.has_work
    assert encontrado.labors == []


@pytest.mark.asyncio
async def test_la_referencia_de_otro_ciclo_no_sirve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Es lo que impide cerrar una reaparición con el trabajo del ciclo anterior.

    Sin el ciclo dentro del identificador harían falta una ventana de fechas y
    un registro de trabajos ya usados; con él, un trabajo marcado sólo puede
    pertenecer al ciclo que lo marcó.
    """
    encontrado = await _buscar(
        monkeypatch, _orden([_labor(f"Ref. Navifault: {_ref(1)}")]), ciclo=2
    )

    assert not encontrado.has_work, "el trabajo del ciclo 1 no cierra el ciclo 2"


@pytest.mark.asyncio
async def test_los_repuestos_se_atan_al_trabajo_que_los_consumio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`parts[].laborId` es lo único que dice qué pieza entró a qué trabajo."""
    fake = _orden(
        [_labor(f"Ref. Navifault: {_ref()}", id=15090)],
        [
            {"id": 24864, "laborId": 15090, "name": "CARCASA ENFRIADOR - 5536735", "qty": 1.0},
            {"id": 24865, "laborId": 99999, "name": "De otro trabajo", "qty": 2.0},
        ],
    )
    encontrado = await _buscar(monkeypatch, fake)

    piezas = encontrado.labors[0].parts
    assert [p.external_id for p in piezas] == [24864]
    assert piezas[0].name == "CARCASA ENFRIADOR - 5536735"
    assert encontrado.labors[0].replaced_component is True


@pytest.mark.asyncio
async def test_sin_repuestos_es_intervencion_y_no_cambio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sale del dato real y no de lo que alguien escribió."""
    encontrado = await _buscar(monkeypatch, _orden([_labor(f"Ref. Navifault: {_ref()}")]))

    assert encontrado.labors[0].replaced_component is False


@pytest.mark.asyncio
async def test_una_orden_que_no_existe_se_reporta_como_tal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CloudFleet responde 404 y el servicio no lo confunde con "sin trabajos"."""

    async def _sin_orden(number: int, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(orden.cloudfleet_service, "get_work_order", _sin_orden)
    with pytest.raises(orden.NavifaultOrderError):
        await orden.find_fault_work(
            vehicle_id=_VEHICULO, signature=_FIRMA, cycle_number=1, work_order_number=99999
        )


@pytest.mark.asyncio
async def test_la_copia_guarda_lo_confirmado_y_no_solo_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anular una orden borra sus trabajos y repuestos.

    Con sólo los identificadores, la gestión quedaría apuntando a nada.
    """
    fake = _orden(
        [_labor(f"Ref. Navifault: {_ref()}", id=15090)],
        [{"id": 24864, "laborId": 15090, "name": "CARCASA ENFRIADOR - 5536735", "qty": 1.0}],
    )
    copia = (await _buscar(monkeypatch, fake)).snapshot()

    assert copia["work_order_number"] == 5733
    trabajo = copia["labors"][0]
    assert trabajo["name"] == "D/M CARCASA DE ENGRANES"
    assert trabajo["system"] == "MOTOR"
    assert trabajo["maintenance_type"] == "Correctivo"
    assert trabajo["parts"][0]["name"] == "CARCASA ENFRIADOR - 5536735"


def test_una_fecha_ilegible_no_descarta_el_trabajo() -> None:
    """El `createdAt` es informativo: el filtro es la referencia, no la fecha."""
    assert orden.parse_cloudfleet_datetime("no es una fecha") is None
    assert orden.parse_cloudfleet_datetime("2026-09-05T18:13:19.2470000Z") == datetime(
        2026, 9, 5, 18, 13, 19, 247000, tzinfo=UTC
    )


@pytest.mark.parametrize(
    ("estado", "termino"),
    [
        ("closed", True),
        ("onTechnicalCompletion", True),
        ("opened", False),
        ("voided", False),
        (None, False),
    ],
)
@pytest.mark.asyncio
async def test_solo_una_orden_terminada_permite_confirmar(
    monkeypatch: pytest.MonkeyPatch, estado: str | None, termino: bool
) -> None:
    """Gestionar es confirmar lo que el taller TERMINÓ.

    Con la orden abierta el registro todavía puede cambiar, así que confirmar
    ahí declararía resuelto algo en curso. Dos casos que no son obvios y por eso
    están fijados: `voided` NO cuenta —anular vacía la orden— y un estado
    **desconocido** tampoco, porque sin saberlo no se puede afirmar que terminó.

    Se prueba `is_finished` y no la lista de estados: es la única autoridad de
    la regla, la consultan el guard de la confirmación y el contrato que la
    pantalla obedece, y comprobar la constante no probaría que se aplica.
    """

    encontrado = await _buscar(
        monkeypatch, _orden([_labor(f"Trabajo de la falla {_ref()}")], status=estado)
    )
    assert encontrado.has_work, "el trabajo marcado se reconoce sea cual sea el estado"
    assert encontrado.is_finished is termino


def _contexto(fuente: str):
    """Contexto mínimo: los dos guards del cierre disparan antes de tocar la base."""
    from app.models.master_data import Vehicle
    from app.services.navifault_management_service import ManagementFaultContext

    firma = ManagedFaultSignature(
        source=fuente,
        diagnostic_code=_FIRMA.diagnostic_code,
        failure_mode=_FIRMA.failure_mode,
        stop_amber=_FIRMA.stop_amber,
        stop_red=_FIRMA.stop_red,
        malfunction=_FIRMA.malfunction,
        warning=_FIRMA.warning,
    )
    return ManagementFaultContext(
        vehicle=Vehicle(plate="TSTE01"), fault_row_id="row-telematica", signature=firma
    )


def test_la_excepcion_telematica_la_acota_la_fuente() -> None:
    """La llave es la FUENTE, no el nombre del controlador.

    `Telematics device` es un nombre para mostrar: puede cambiar o traducirse.
    El `strip` está fijado porque la fuente llega normalizada desde el hecho y
    un espacio de más convertiría una falla del equipo en una del vehículo.
    """

    assert orden.is_telematics_fault(_contexto("SourceGeotabGoId").signature) is True
    assert orden.is_telematics_fault(_contexto("  SourceGeotabGoId  ").signature) is True
    assert orden.is_telematics_fault(_contexto("SourceJ1939Id").signature) is False


@pytest.mark.asyncio
async def test_una_falla_del_vehiculo_no_se_cierra_con_nota() -> None:
    """El agujero que esto tapa: cerrar con nota es la ÚNICA vía sin orden.

    Si aceptara una falla del bus del vehículo, cualquiera podría gestionar sin
    que el taller hubiera registrado nada, y el módulo entero dejaría de tener
    una sola fuente de verdad.
    """

    with pytest.raises(orden.NavifaultConfirmationError, match="orden"):
        await orden.close_telematics_fault(
            None,  # type: ignore[arg-type]
            context=_contexto("SourceJ1939Id"),
            actor=None,  # type: ignore[arg-type]
            note="lo que sea",
        )


@pytest.mark.asyncio
async def test_el_cierre_telematico_exige_nota() -> None:
    """Sin orden, sin trabajos y sin repuestos, la nota es lo único que queda.

    Un cierre en blanco sacaría la falla de la bandeja sin que nadie pudiera
    decir por qué. Se prueba con espacios: `strip` es lo que lo impide.
    """

    with pytest.raises(orden.NavifaultConfirmationError, match="nota"):
        await orden.close_telematics_fault(
            None,  # type: ignore[arg-type]
            context=_contexto("SourceGeotabGoId"),
            actor=None,  # type: ignore[arg-type]
            note="   ",
        )


def test_la_evidencia_entre_flotas_es_anonima() -> None:
    """Mira el CONTRATO, no la implementación: es por donde se escaparía.

    La evidencia cruza todas las flotas porque el conocimiento técnico sobre un
    código es de Navitrans, mientras que el dato operativo es del cliente. Eso
    sólo es defendible si lo que se publica no tiene dueño.

    El número de orden entra en la lista de prohibidos a propósito: identifica
    al taller y a la flota tan bien como una placa.
    """

    from app.schemas.navifault import (
        NavifaultFaultEvidenceEntry,
        NavifaultOrderLaborRead,
        NavifaultOrderPartRead,
    )

    prohibidos = {
        "plate", "placa", "movil", "vehicle", "vehicle_id", "vehicle_code",
        "fleet", "fleet_id", "actor", "author", "user_id", "managed_by",
        "last_managed_by", "case_id", "work_order_number", "reference",
    }
    for schema in (
        NavifaultFaultEvidenceEntry,
        NavifaultOrderLaborRead,
        NavifaultOrderPartRead,
    ):
        filtrados = set(schema.model_fields) & prohibidos
        assert not filtrados, f"{schema.__name__} publica campos que identifican: {filtrados}"


def test_la_verificacion_admite_no_saberlo_todavia() -> None:
    """Tres estados y no dos.

    Sin `pending`, un cierre de ayer tendría que contarse como éxito o como
    fracaso, y las dos cosas serían falsas: la ventana de verificación no ha
    transcurrido. Afirmar que funcionó antes de que pase es exactamente lo que
    esa ventana existe para impedir.
    """

    from typing import get_args

    from app.schemas.navifault import NavifaultFaultEvidenceEntry

    anotacion = NavifaultFaultEvidenceEntry.model_fields["verification"].annotation
    assert set(get_args(anotacion)) == {"held", "returned", "pending"}
