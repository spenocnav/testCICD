"""Invariantes de la comunicación cliente de Navifault.

Estas pruebas son puras: fijan el contrato de la salida del modelo, que es
donde falló el flujo real. Un modelo con razonamiento agotaba el presupuesto
pensando y devolvía `content` vacío, y el validador rechazaba además salidas
correctas cuyos dos párrafos venían separados por un solo salto de línea.
"""

from __future__ import annotations

import json

import pytest

from app.services.navifault_client_description_service import (
    NavifaultClientDescriptionError,
    _parse_output,
    _split_paragraphs,
)

_CORREO = "Se detectó una falla. La lámpara se encendió. Revise el vehículo."


def _payload(correo: str, plataforma: str) -> str:
    return json.dumps(
        {
            "descripcion_correo_cliente": correo,
            "descripcion_plataforma_cliente": plataforma,
        },
        ensure_ascii=False,
    )


def test_split_paragraphs_acepta_linea_en_blanco() -> None:
    assert _split_paragraphs("Primero.\n\nSegundo.") == ["Primero.", "Segundo."]


def test_split_paragraphs_acepta_salto_simple() -> None:
    """El modelo alterna entre ambos separadores según la corrida."""
    assert _split_paragraphs("Primero.\nSegundo.") == ["Primero.", "Segundo."]


def test_split_paragraphs_conserva_un_solo_parrafo() -> None:
    assert _split_paragraphs("Uno solo.") == ["Uno solo."]


@pytest.mark.parametrize("separador", ["\n\n", "\n"])
def test_parse_output_normaliza_los_dos_parrafos(separador: str) -> None:
    correo, plataforma = _parse_output(
        _payload(_CORREO, f"Primer párrafo.{separador}Segundo párrafo.")
    )
    assert correo == _CORREO
    # La salida almacenada siempre queda con el separador canónico.
    assert plataforma == "Primer párrafo.\n\nSegundo párrafo."


def test_parse_output_rechaza_un_solo_parrafo() -> None:
    with pytest.raises(NavifaultClientDescriptionError, match="2 párrafos"):
        _parse_output(_payload(_CORREO, "Un único párrafo sin corte."))


def test_parse_output_rechaza_correo_de_dos_oraciones() -> None:
    with pytest.raises(NavifaultClientDescriptionError, match="3 o 4 oraciones"):
        _parse_output(_payload("Una. Dos.", "Primero.\n\nSegundo."))


def test_parse_output_rechaza_salida_vacia() -> None:
    """`content` vacío es el síntoma de un presupuesto de salida agotado."""
    with pytest.raises(NavifaultClientDescriptionError, match="JSON válido"):
        _parse_output("")


def test_error_de_contrato_conserva_la_salida_para_el_reintento() -> None:
    """El reintento sólo procede si el modelo llegó a redactar algo.

    ``raw_output`` es lo que distingue "respondió mal" de "no respondió": sin
    salida no hay motivo que devolverle, y repetir la llamada daría lo mismo.
    """
    salida = _payload(_CORREO, "Un único párrafo.")
    with pytest.raises(NavifaultClientDescriptionError) as capturado:
        _parse_output(salida)
    # _parse_output no adjunta raw_output; lo hace _call_llm al re-lanzarlo.
    error = NavifaultClientDescriptionError(
        str(capturado.value), raw_output=salida, diagnostics={"finish_reason": "stop"}
    )
    assert error.raw_output == salida
    assert error.diagnostics["finish_reason"] == "stop"


def test_presupuesto_agotado_no_deja_salida_que_corregir() -> None:
    error = NavifaultClientDescriptionError(
        "El modelo agotó el presupuesto de salida antes de responder",
        raw_output="",
        diagnostics={"finish_reason": "length", "reasoning_chars": 3878},
    )
    assert not error.raw_output
    assert error.diagnostics["finish_reason"] == "length"


@pytest.mark.asyncio
async def test_los_carriles_no_se_pisan() -> None:
    """Cada carril ve sólo su mitad de la cola.

    Es lo que garantiza que el barrido no ocupe el slot reservado a la ficha
    que un usuario acaba de abrir, y que un slot libre no se quede esperando
    trabajo que le corresponde al otro carril.
    """
    from app.db.session import AsyncSessionLocal
    from app.models.navifault import (
        NavifaultFaultPage,
        NavifaultGeneratedClientDescription,
        NavifaultManual,
    )
    from app.services.navifault_client_description_service import (
        INTERACTIVE_PRIORITY,
        SWEEP_PRIORITY,
        claim_one,
    )

    async with AsyncSessionLocal() as session:
        session.add(
            NavifaultManual(
                pub_id="pub-test",
                language="ES",
                engine_models=[],
                fault_pages_total=1,
                source_status="ready",
            )
        )
        # El manual tiene que existir antes que la página: son entidades sueltas
        # y SQLAlchemy no puede deducir el orden sin una relación declarada.
        await session.flush()
        session.add(
            NavifaultFaultPage(
                fault_page_id="page-test",
                pub_id="pub-test",
                language="ES",
                fault_code=1234,
                variant="",
                source_url="https://example.invalid/pub-test/1234",
                raw_html_status="available",
                summary={},
                sections={},
                source_record={},
            )
        )
        await session.flush()
        for suffix, priority in (("inter", INTERACTIVE_PRIORITY), ("fondo", SWEEP_PRIORITY)):
            session.add(
                NavifaultGeneratedClientDescription(
                    description_id=f"desc-{suffix}",
                    fault_page_id="page-test",
                    context_sha256=f"sha-{suffix}",
                    prompt_version="v-test",
                    model_version="m-test",
                    status="pending",
                    priority=priority,
                    generation_metadata={},
                )
            )
        await session.commit()

        interactivo = await claim_one(session, lane="interactive")
        assert interactivo is not None
        assert interactivo.description_id == "desc-inter"

        # El carril interactivo ya no tiene trabajo: no debe tomar el de fondo.
        assert await claim_one(session, lane="interactive") is None

        fondo = await claim_one(session, lane="background")
        assert fondo is not None
        assert fondo.description_id == "desc-fondo"
