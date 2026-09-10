"""Clasificaciones canónicas de hábitos seguros declaradas por la fuente maestra."""

from __future__ import annotations

import pytest
import pandas as pd

import extract.extract_habitos as extract_habitos
import extract.extract_dimensions as extract_dimensions
import transform.transform_habitos as transform_habitos
from extract.extract_habitos import (
    ACCEL_FWD_ID,
    ACCEL_SIDE_ID,
    ACCEL_UP_ID,
    LOAD_DIAG_ID,
    RPM_DIAG_ID,
    _diags_for_event_type,
)


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("Excesos de velocidad", []),
        ("Giros bruscos", [ACCEL_SIDE_ID]),
        ("Excesos de RPM", [RPM_DIAG_ID, LOAD_DIAG_ID]),
        ("Frenadas bruscas", [ACCEL_FWD_ID]),
        ("Baches o Resaltos fuertes", [ACCEL_UP_ID]),
        ("Aceleraciones bruscas", [ACCEL_FWD_ID]),
    ],
)
def test_safe_habit_descriptions_select_diagnostics(
    description: str, expected: list[str]
) -> None:
    assert _diags_for_event_type(description) == expected


def test_derived_rpm_rule_is_motor_scoped_and_deduplicated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        extract_habitos,
        "EVENT_RULES",
        {"base_x": {"Frenadas bruscas": "BRAKE1"}},
    )
    monkeypatch.setattr(
        extract_habitos,
        "EVENT_RULES_BY_MOTOR",
        {"base_x": {"X13": {"Excesos de RPM": "RPM1"}}},
    )
    monkeypatch.setattr(
        extract_habitos,
        "_MOTOR_BY_DEVICE",
        {
            ("base_x", "device-x13"): "X13",
            ("base_x", "device-x15"): "X15",
        },
    )
    monkeypatch.setattr(
        extract_habitos,
        "HABITOS_RPM_DEVICE_CLASS",
        {
            ("base_x", "device-x13"): "rpm-class",
            ("base_x", "device-x15"): "rpm-class",
        },
    )
    monkeypatch.setattr(extract_habitos, "RPM_RULES", {"rpm-class": ["RPM1"]})

    x13_rules = extract_habitos._get_rules_for_device("device-x13", "base_x")
    assert x13_rules == {
        "Frenadas bruscas": "BRAKE1",
        "Excesos de RPM": "RPM1",
    }
    assert list(x13_rules.values()).count("RPM1") == 1

    x15_rules = extract_habitos._get_rules_for_device("device-x15", "base_x")
    assert x15_rules == {
        "Frenadas bruscas": "BRAKE1",
        "Exceso RPM": "RPM1",
    }


def test_motor_scoped_rpm_application_builds_habit_dimension_and_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scoped = {"base_x": {"X13": {"Excesos de RPM": "RPM1"}}}
    monkeypatch.setattr(extract_dimensions, "RULES_BY_MOTOR", {})
    monkeypatch.setattr(extract_dimensions, "EVENT_RULES", {})
    monkeypatch.setattr(extract_dimensions, "EVENT_RULES_BY_MOTOR", scoped)
    monkeypatch.setattr(extract_dimensions, "RPM_RULES", {})

    dim_rule = extract_dimensions.build_dim_reglas()
    row = dim_rule.iloc[0]
    assert row["rule_name"] == "Excesos de RPM"
    assert row["scope_type"] == "database_motor"
    assert row["scope_value"] == "base_x::X13"
    assert row["categoria"] == "Seguridad"

    monkeypatch.setattr(transform_habitos, "EVENT_RULES_BY_MOTOR", scoped)
    rule_sk = transform_habitos._resolve_rule_sk(
        pd.Series(
            {
                "database_name": "base_x",
                "motor_type": "X13",
                "rule_id": "RPM1",
                "rpm_class": None,
            }
        ),
        {("RPM1", "database_motor", "base_x::X13"): "RULE-SK"},
    )
    assert rule_sk == "RULE-SK"
