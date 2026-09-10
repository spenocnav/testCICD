"""Bandas de RPM en config_source: banda declarada en DB vs. inferencia por nombre.

La fuente maestra (`geotab_rule_applications.band` / `.is_descenso`) manda; el
matcher por palabra clave queda solo como fallback y debe avisar. Ninguna regla
de operación puede quedar fuera del cálculo de rangos en silencio.

Sin DB ni red: se prueba `_resolve_band` directo y `_build` contra un engine de
mentira.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from config_source import (
    _BAND_BY_ENUM,
    _build,
    _canonical_band,
    _resolve_band,
    _with_descenso,
)


# ---------------------------------------------------------------------------
# Mapeo enum -> nombre canónico
# ---------------------------------------------------------------------------
def test_enum_covers_the_seven_bands() -> None:
    assert set(_BAND_BY_ENUM) == {
        "rango_bajo",
        "rango_economico",
        "rango_balanceado",
        "rango_potencia",
        "rango_potencia_ineficiente",
        "exceso_rpm",
        "ralenti",
    }


@pytest.mark.parametrize(
    ("enum_value", "canonical"),
    [
        ("rango_bajo", "Rango Bajo"),
        ("rango_economico", "Rango Economico"),
        ("rango_balanceado", "Rango Balanceado"),
        ("rango_potencia", "Rango Potencia"),
        ("rango_potencia_ineficiente", "Rango Potencia Ineficiente"),
        ("exceso_rpm", "Exceso RPM"),
        ("ralenti", "Ralentí"),
    ],
)
def test_band_enum_maps_to_transform_names(enum_value: str, canonical: str) -> None:
    assert _resolve_band("nombre irrelevante", enum_value, False) == canonical


@pytest.mark.parametrize(
    ("enum_value", "canonical"),
    [
        ("rango_bajo", "Rango Bajo Descenso"),
        ("rango_economico", "Rango Economico Descenso"),
        ("rango_balanceado", "Rango Balanceado Descenso"),
        ("rango_potencia", "Rango Potencia Descenso"),
        ("rango_potencia_ineficiente", "Rango Potencia Ineficiente Descenso"),
        ("exceso_rpm", "Exceso RPM Descenso"),
    ],
)
def test_is_descenso_adds_suffix(enum_value: str, canonical: str) -> None:
    assert _resolve_band("nombre irrelevante", enum_value, True) == canonical


def test_ralenti_never_gets_descenso_suffix() -> None:
    # Los transforms no arman la dimensión 'Ralentí Descenso'.
    assert _resolve_band("Ralentí en descenso", "ralenti", True) == "Ralentí"
    assert _with_descenso("Ralentí", True) == "Ralentí"


def test_band_from_db_is_case_and_space_insensitive() -> None:
    assert _resolve_band("x", "  Rango_Economico  ", False) == "Rango Economico"


# ---------------------------------------------------------------------------
# Prioridad DB > keyword, fallback y exclusión explícita
# ---------------------------------------------------------------------------
def test_db_band_wins_over_contradicting_name() -> None:
    """Regla llamada 'Rango Bajo' pero declarada como potencia: gana la DB."""
    assert _resolve_band("Rango Bajo X11", "rango_potencia", False) == "Rango Potencia"


def test_db_descenso_wins_over_name_without_descenso() -> None:
    assert _resolve_band("Rango Bajo X11", "rango_bajo", True) == "Rango Bajo Descenso"


def test_null_band_falls_back_to_keyword_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="config_source"):
        assert _resolve_band("Rango Consumo X11", None, False, "base_x", "R7") == (
            "Rango Potencia Ineficiente"
        )
    assert any("sin banda declarada" in rec.getMessage() for rec in caplog.records)
    assert any("R7" in rec.getMessage() for rec in caplog.records)


def test_unknown_db_band_falls_back_to_keyword_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="config_source"):
        assert _resolve_band("Rango Bajo X11", "rango_inventado", False) == "Rango Bajo"
    assert any("banda desconocida" in rec.getMessage() for rec in caplog.records)


def test_unresolvable_rule_warns_and_is_excluded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="config_source"):
        assert _resolve_band("Regla renombrada por el cliente", None, False, "base_x", "R9") is None
    messages = [rec.getMessage() for rec in caplog.records]
    assert any("excluida del cálculo de rangos" in msg for msg in messages)
    assert any("R9" in msg for msg in messages)


# ---------------------------------------------------------------------------
# No regresión del matcher por palabra clave (reglas de negocio confirmadas)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("rule_name", "canonical"),
    [
        ("Rango Bajo X11", "Rango Bajo"),
        ("Rango Económico ISD", "Rango Economico"),
        ("Potencia Eficiente", "Rango Potencia"),  # 'eficiente' NO gana a 'potencia'
        ("Rango Consumo X11", "Rango Potencia Ineficiente"),
        ("Potencia Ineficiente", "Rango Potencia Ineficiente"),
        ("Rango Balanceado S13", "Rango Balanceado"),
        ("Exceso de RPM", "Exceso RPM"),
        ("Ralentí prolongado", "Ralentí"),
        ("Rango Bajo en Descenso", "Rango Bajo Descenso"),
        ("Ralentí en Descenso", "Ralentí"),
        ("Frenada brusca", None),
    ],
)
def test_keyword_matcher_unchanged(rule_name: str, canonical: str | None) -> None:
    assert _canonical_band(rule_name) == canonical


# ---------------------------------------------------------------------------
# _build: desempaque de la query y construcción de RULES_BY_MOTOR
# ---------------------------------------------------------------------------
class _FakeResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[Any, ...]]:
        return self._rows


class _FakeConn:
    """Devuelve resultados en el orden en que `_build` corre sus queries."""

    def __init__(self, results: list[list[tuple[Any, ...]]]) -> None:
        self._results = list(results)

    def execute(self, *_args: Any, **_kwargs: Any) -> _FakeResult:
        return _FakeResult(self._results.pop(0))

    def __enter__(self) -> _FakeConn:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


class _FakeEngine:
    def __init__(self, results: list[list[tuple[Any, ...]]]) -> None:
        self._results = results

    def connect(self) -> _FakeConn:
        return _FakeConn(self._results)


class _FakeFernet:
    def decrypt(self, _value: bytes) -> bytes:
        return b"pw"


# (database_name, device_id, placa, motor_type, group_key, rpm_class,
#  tank_volume, tipo_combustible, range_mode, fleet_code, ralenti_analysis_enabled)
_VEHICLE_ROW = (
    "base_x", "dev1", "ABC123", "X11", "grp", "clase", 100.0, None, "reglas", "FLOTA-X", False
)


def _build_all(
    *,
    vehicles: list[tuple[Any, ...]] | None = None,
    motor_rows: list[tuple[Any, ...]] | None = None,
    event_rows: list[tuple[Any, ...]] | None = None,
    rpm_band_rows: list[tuple[Any, ...]] | None = None,
    creds: list[tuple[Any, ...]] | None = None,
) -> dict[str, Any]:
    """Corre `_build` con las 6 queries en el mismo orden que el código."""
    creds = creds if creds is not None else [("base_x", "user@test", b"enc")]
    engine = _FakeEngine(
        [
            vehicles if vehicles is not None else [_VEHICLE_ROW],
            creds,
            motor_rows or [],
            event_rows or [],
            [],
            rpm_band_rows or [],
        ]
    )
    result = _build(engine, _FakeFernet())
    assert result is not None
    return result


def _build_with(motor_rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    return _build_all(motor_rows=motor_rows)


def _build_with_events(event_rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    return _build_all(event_rows=event_rows)


def test_build_uses_declared_band_over_name() -> None:
    result = _build_with(
        [
            # (motor_type, name, rule_id, band, is_descenso, database_name)
            ("X11", "Rango Bajo X11", "R1", "rango_potencia", False, "base_x"),
            ("X11", "Nombre sin pistas", "R2", "rango_bajo", True, "base_x"),
        ]
    )
    assert result["rules_by_motor"]["X11"] == {
        "Rango Potencia": "R1",
        "Rango Bajo Descenso": "R2",
    }


def test_build_falls_back_to_keyword_when_band_is_null() -> None:
    result = _build_with(
        [("X11", "Rango Consumo X11", "R1", None, False, "base_x")]
    )
    assert result["rules_by_motor"]["X11"] == {"Rango Potencia Ineficiente": "R1"}


def test_build_excludes_unresolvable_rule_but_keeps_the_rest(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="config_source"):
        result = _build_with(
            [
                ("X11", "Rango Bajo X11", "R1", "rango_bajo", False, "base_x"),
                ("X11", "Regla renombrada", "R2", None, False, "base_x"),
            ]
        )
    assert result["rules_by_motor"]["X11"] == {"Rango Bajo": "R1"}
    messages = [rec.getMessage() for rec in caplog.records]
    assert any("excluida del cálculo de rangos" in msg for msg in messages)


def test_build_scopes_bands_by_database_not_only_by_motor() -> None:
    """Dos clientes con el mismo motor tienen reglas propias en su propia base.

    Regresión: al indexar solo por motor, la última base leída pisaba los
    rule_id de la anterior. El extractor terminaba pidiéndole a una base los
    ids de la otra, Geotab devolvía vacío y esas bandas quedaban en cero,
    inflando el porcentaje de las que sí matcheaban.
    """
    result = _build_with(
        [
            ("X13E6", "Rango Bajo X13", "RIVER_BAJO", "rango_bajo", False, "rivercol"),
            ("X13E6", "Rango Potencia X13", "RIVER_POT", "rango_potencia", False, "rivercol"),
            ("X13E6", "Rango Bajo X13", "OTRA_BAJO", "rango_bajo", False, "otra_base"),
            ("X13E6", "Rango Potencia X13", "OTRA_POT", "rango_potencia", False, "otra_base"),
        ]
    )
    assert result["rules_by_db_motor"]["rivercol"]["X13E6"] == {
        "Rango Bajo": "RIVER_BAJO",
        "Rango Potencia": "RIVER_POT",
    }
    assert result["rules_by_db_motor"]["otra_base"]["X13E6"] == {
        "Rango Bajo": "OTRA_BAJO",
        "Rango Potencia": "OTRA_POT",
    }


def test_build_warns_when_one_database_declares_a_band_twice(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="config_source"):
        result = _build_with(
            [
                ("X11", "Rango Bajo X11", "R1", "rango_bajo", False, "base_x"),
                ("X11", "Rango Bajo viejo", "R2", "rango_bajo", False, "base_x"),
            ]
        )
    assert result["rules_by_db_motor"]["base_x"]["X11"] == {"Rango Bajo": "R1"}
    assert any(
        "duplicada en base=base_x" in rec.getMessage() for rec in caplog.records
    )


def test_build_uses_safe_habit_description_as_event_label() -> None:
    result = _build_with_events(
        [
            # (database, description, event_type, physical_name, rule_id, motor)
            ("base_x", "Frenadas bruscas", None, "Regla 1", "H1", None),
            ("base_x", "Excesos de velocidad", None, "Regla 2", "H2", None),
            (
                "base_x",
                "Excesos de RPM",
                "exceso_rpm",
                "Nombre irrelevante",
                "RPM1",
                "X11",
            ),
            (
                "base_x",
                None,
                "exceso_rpm",
                "No contiene palabras de RPM",
                "RPM2",
                "X15",
            ),
        ]
    )
    assert result["event_rules"]["base_x"] == {
        "Frenadas bruscas": "H1",
        "Excesos de velocidad": "H2",
    }
    assert result["event_rules_by_motor"] == {
        "base_x": {
            "X11": {"Excesos de RPM": "RPM1"},
            "X15": {"Excesos de RPM": "RPM2"},
        }
    }


# ---------------------------------------------------------------------------
# Rangos de RPM por motor (`motor_rpm_bands`) — modo range_mode='rpm'
# ---------------------------------------------------------------------------
def _rpm_band_rows(motor: str = "X11", *, exceso_max: int | None = None) -> list[tuple[Any, ...]]:
    """(motor_type, band, rpm_min, rpm_max) con la partición del reporte base."""
    return [
        (motor, "rango_bajo", 600, 1100),
        (motor, "rango_economico", 1100, 1450),
        (motor, "rango_balanceado", 1450, 1800),
        (motor, "rango_potencia", 1800, 2300),
        (motor, "rango_potencia_ineficiente", 2300, 2750),
        (motor, "exceso_rpm", 2750, exceso_max),
    ]


def test_build_exposes_rpm_bands_with_canonical_names() -> None:
    result = _build_all(rpm_band_rows=_rpm_band_rows())
    bands = result["rpm_bands_by_motor"]["X11"]
    assert [band["band"] for band in bands] == [
        "Rango Bajo",
        "Rango Economico",
        "Rango Balanceado",
        "Rango Potencia",
        "Rango Potencia Ineficiente",
        "Exceso RPM",
    ]
    assert bands[0] == {"band": "Rango Bajo", "rpm_min": 600, "rpm_max": 1100}
    assert bands[-1]["rpm_max"] is None


def test_build_carries_fleet_range_mode_into_the_catalog() -> None:
    result = _build_all(
        vehicles=[
            ("base_x", "dev1", "ABC123", "X11", "grp", "clase", 100.0, None, "rpm", "FLOTA-X", False)
        ]
    )
    assert result["vehicle_catalog"][0]["range_mode"] == "rpm"


def test_build_carries_fleet_ralenti_analysis_flag_into_the_catalog() -> None:
    """El flag de Análisis de Ralentí es de la flota y viaja en cada vehículo:
    es lo único que autoriza a extract_ralenti a consultar Geotab por él."""
    result = _build_all(
        vehicles=[
            ("base_x", "dev1", "ABC123", "X11", "grp", "clase", 100.0, None, "reglas", "FLOTA-X", True),
            ("base_x", "dev2", "ABC124", "X11", "grp", "clase", 100.0, None, "reglas", "FLOTA-Y", None),
        ]
    )
    by_device = {v["device_id"]: v for v in result["vehicle_catalog"]}
    assert by_device["dev1"]["ralenti_analysis"] is True
    assert by_device["dev1"]["fleet_code"] == "FLOTA-X"
    assert by_device["dev2"]["ralenti_analysis"] is False


def test_build_defaults_missing_range_mode_to_reglas() -> None:
    result = _build_all(
        vehicles=[
            ("base_x", "dev1", "ABC123", "X11", "grp", "clase", 100.0, None, None, "FLOTA-X", False)
        ]
    )
    assert result["vehicle_catalog"][0]["range_mode"] == "reglas"


@pytest.mark.parametrize(
    ("rows", "reason"),
    [
        pytest.param(_rpm_band_rows()[:3], "no cubre el eje", id="incompleta"),
        pytest.param(
            [
                (motor, band, 1200 if band == "rango_economico" else rpm_min, rpm_max)
                for motor, band, rpm_min, rpm_max in _rpm_band_rows()
            ],
            "inconsistentes",
            id="con-hueco",
        ),
        pytest.param(
            [
                (motor, band, rpm_min, 1200 if band == "rango_bajo" else rpm_max)
                for motor, band, rpm_min, rpm_max in _rpm_band_rows()
            ],
            "inconsistentes",
            id="con-solape",
        ),
        pytest.param(
            [
                (motor, band, rpm_min, None if band == "rango_balanceado" else rpm_max)
                for motor, band, rpm_min, rpm_max in _rpm_band_rows()
            ],
            "inconsistentes",
            id="banda-intermedia-abierta",
        ),
    ],
)
def test_build_drops_motor_with_broken_rpm_partition(
    rows: list[tuple[Any, ...]], reason: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Sin partición utilizable el motor queda fuera: mejor no calcular que
    repartir tiempo con cortes que no son suyos."""
    with caplog.at_level(logging.WARNING, logger="config_source"):
        result = _build_all(rpm_band_rows=rows)
    assert result["rpm_bands_by_motor"] == {}
    assert any(reason in record.getMessage() for record in caplog.records)


def test_build_drops_motor_with_unknown_rpm_band_even_if_the_rest_is_complete(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rows = _rpm_band_rows() + [("X11", "banda_inventada", 100, 200)]
    with caplog.at_level(logging.WARNING, logger="config_source"):
        result = _build_all(rpm_band_rows=rows)
    assert result["rpm_bands_by_motor"] == {}
    assert any("desconocida" in record.getMessage() for record in caplog.records)


def test_build_keeps_healthy_motor_when_another_is_broken() -> None:
    rows = _rpm_band_rows("X11") + _rpm_band_rows("S13")[:2]
    result = _build_all(rpm_band_rows=rows)
    assert set(result["rpm_bands_by_motor"]) == {"X11"}


# ---------------------------------------------------------------------------
# _build: credenciales — una entrada por (base, usuario)
# ---------------------------------------------------------------------------
def test_build_dedupes_same_account_replicated_across_fleets() -> None:
    """La misma cuenta bajo N flotas que comparten la base es UNA credencial.

    geotab_databases tiene una fila por flota; sin el corte, `navitrans` armaba
    12 entradas con 2 usuarios reales y los extractores lanzaban 12 hilos sobre
    2 sesiones: paralelismo falso y más OverLimit.
    """
    result = _build_all(
        creds=[
            ("base_x", "a@test", b"enc"),
            ("base_x", "a@test", b"enc"),
            ("base_x", "a@test", b"enc"),
            ("base_x", "b@test", b"enc"),
        ]
    )
    users = [c["username"] for c in result["credentials"]["base_x"]]
    assert users == ["a@test", "b@test"]


def test_build_keeps_distinct_accounts_of_one_database() -> None:
    result = _build_all(
        creds=[
            ("base_x", "a@test", b"enc"),
            ("base_x", "b@test", b"enc"),
            ("base_x", "c@test", b"enc"),
        ]
    )
    assert len(result["credentials"]["base_x"]) == 3


def test_build_same_username_in_two_databases_is_two_credentials() -> None:
    result = _build_all(
        vehicles=[_VEHICLE_ROW, _VEHICLE_ROW[:0] + ("base_y",) + _VEHICLE_ROW[1:]],
        creds=[("base_x", "a@test", b"enc"), ("base_y", "a@test", b"enc")],
    )
    assert len(result["credentials"]["base_x"]) == 1
    assert len(result["credentials"]["base_y"]) == 1
