"""La referencia que ata los trabajos a la novedad, y el texto que la lleva.

CloudFleet **no publica** el enlace entre una novedad y el trabajo que la
resuelve: lo pinta en su interfaz con un distintivo y no lo expone por API en
ninguna de las dos direcciones. Comprobado el 2026-09-05 sobre la orden 5733,
volcando el objeto completo del trabajo. Y sólo admite un trabajo por novedad.

La referencia resuelve las dos cosas a la vez: el taller la copia en cada
trabajo que corresponda, y esa copia es una declaración explícita de una
persona, no una coincidencia de texto que hubiera que interpretar.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.services.navifault_escalation_service import build_escalation_comment
from app.services.navifault_management_service import (
    ManagedFaultSignature,
    navifault_reference,
)

_VEHICULO = uuid.UUID("11111111-2222-3333-4444-555555555555")
_FIRMA = ManagedFaultSignature(
    source="SourceJ1939Id",
    diagnostic_code=1347,
    failure_mode=3.0,
    stop_amber=True,
    stop_red=False,
    malfunction=True,
    warning=False,
)


def _ref(vehicle_id=_VEHICULO, signature=_FIRMA) -> str:
    return navifault_reference(vehicle_id=vehicle_id, signature=signature)

_FILA = {
    "codigo_diagnostico": 3031,
    "codigo_modo_de_falla": 9.0,
    "diagnostico": "Postratamiento 1 temperatura del tanque de DEF",
    "modo_de_falla": "Abnormal update rate",
    "nombre_de_controlador": "Engine #1",
    "tipo_de_atencion": "Nivel 2 - Prioritaria",
    "luz_de_parada_roja": False,
    "luz_de_parada_amber": True,
    "lampara_de_averia": False,
    "lampara_de_advertencia": False,
    "fecha_de_falla": datetime(2026, 9, 5, 12, 46, tzinfo=UTC),
    "recuento_de_fallos": 126,
}


def _comentario(**kwargs):
    base = {"fault_row": _FILA, "plate": "JTX904", "user_comment": "Prueba"}
    base.update(kwargs)
    return build_escalation_comment(**base)


def test_el_texto_se_lee_igual_sin_saltos_de_linea() -> None:
    """CloudFleet copia el comentario al trabajo ELIMINANDO los saltos.

    Verificado sobre la orden 5733: los 11 saltos desaparecieron y no se
    sustituyeron por nada, así que con ellos como única separación los campos
    quedaban pegados (`JTX904Código:`, `FMI 9Diagnóstico:`).
    """
    plano = _comentario().replace("\n", "")

    for pegado in ("JTX904Código", "9Diagnóstico", "#1Lámparas", "rateControlador"):
        assert pegado not in plano, f"los campos se pegan: {pegado}"
    # Y los campos siguen siendo reconocibles uno por uno.
    for campo in ("Vehículo: JTX904", "Código: 3031", "Controlador: Engine #1"):
        assert campo in plano


def test_la_referencia_sobrevive_a_la_copia() -> None:
    """Es lo único que hace falta que llegue intacto al trabajo."""
    ref = _ref()
    texto = _comentario(reference=ref)

    assert ref in texto
    assert ref in texto.replace("\n", ""), "debe seguir ahí sin saltos de línea"


def test_sin_referencia_el_texto_no_la_menciona() -> None:
    """Una novedad creada antes de esta serie no lleva referencia y es válida."""
    assert "Ref. Navifault" not in _comentario()


def test_el_comentario_del_usuario_va_primero() -> None:
    """Es lo único que aporta contexto que el sistema no conoce."""
    assert _comentario().startswith("Prueba")


def test_la_referencia_es_legible_al_transcribirla() -> None:
    """Una persona la copia a mano: fuera los caracteres que se confunden."""
    refs = [
        navifault_reference(vehicle_id=uuid.uuid4(), signature=_FIRMA) for _ in range(200)
    ]

    for ref in refs:
        assert ref.startswith("NF-")
        cuerpo = ref[3:]
        assert len(cuerpo) == 8
        assert not set(cuerpo) & set("01OIL"), f"carácter ambiguo en {ref}"
    assert len(set(refs)) == len(refs), "vehículos distintos, referencias distintas"


def test_la_referencia_es_la_misma_para_la_misma_falla() -> None:
    """Se deriva y no se guarda: existe desde que la falla aparece.

    Eso es lo que permite copiarla en una orden **sin haber escalado**, y que
    siga siendo la misma si el caso se borra y se vuelve a crear.
    """
    assert _ref() == _ref()


def test_dos_fallas_distintas_no_comparten_referencia() -> None:
    """La identidad es vehículo + firma, la misma con la que el caso es único."""
    otro_codigo = ManagedFaultSignature(
        source=_FIRMA.source,
        diagnostic_code=1348,
        failure_mode=_FIRMA.failure_mode,
        stop_amber=_FIRMA.stop_amber,
        stop_red=_FIRMA.stop_red,
        malfunction=_FIRMA.malfunction,
        warning=_FIRMA.warning,
    )
    assert _ref() != _ref(signature=otro_codigo), "otro código, otra referencia"
    assert _ref() != _ref(vehicle_id=uuid.uuid4()), "otro vehículo, otra referencia"


def test_el_ciclo_cambia_la_referencia() -> None:
    """Un ciclo nuevo estrena identificador, y por eso sobran las reglas de fecha.

    Con la referencia estable de por vida, una orden vieja llevaba la misma
    marca que una nueva y un ciclo podía cerrarse con trabajo de otro. Con el
    ciclo dentro, un trabajo marcado sólo puede pertenecer al ciclo que lo marcó.
    """
    ciclo1 = navifault_reference(vehicle_id=_VEHICULO, signature=_FIRMA, cycle_number=1)
    ciclo2 = navifault_reference(vehicle_id=_VEHICULO, signature=_FIRMA, cycle_number=2)

    assert ciclo1 != ciclo2
    assert ciclo1 == _ref(), "sin ciclo explícito, la falla está en el primero"


def test_una_repeticion_conserva_el_identificador() -> None:
    """Repetida es el MISMO ciclo: la gestión no logró cerrarlo.

    El contador sólo avanza cuando la primera reaparición llega fuera de la
    ventana de 30 días. Una repetición dentro de ella es el segundo intento
    sobre lo mismo, y el trabajo nuevo pertenece al ciclo que falló.
    """
    repetida = navifault_reference(vehicle_id=_VEHICULO, signature=_FIRMA, cycle_number=1)

    assert repetida == _ref()
