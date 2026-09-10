from datetime import datetime

from label_config import normalize_label, parse_label_comment


def test_ids_and_text():
    assert normalize_label("3") == (3, "En diagnóstico")
    assert normalize_label("En diagnóstico") == (3, "En diagnóstico")
    assert normalize_label("En intevencion") == (9, "En intervención")


def test_controlled_comments():
    assert parse_label_comment("9")[0:2] == (9, "En intervención")
    assert parse_label_comment("Etiqueta: En diagnóstico")[0:2] == (3, "En diagnóstico")


def test_explicit_date():
    label_id, _, event_at, display = parse_label_comment("Etiqueta: 10 | Fecha: 05/08/2026 14:30")
    assert label_id == 10
    assert display == "05/08/2026 14:30"
    assert event_at.hour == 19  # 14:30 America/Bogota converted to UTC


def test_explicit_date_without_pipe():
    for comment in ("Etiqueta: 9 Fecha: 05/08/2026 14:30", "En intervencion 05/08/2026 14:30", "9 05-08-2026 14:30"):
        label_id, _, event_at, display = parse_label_comment(comment)
        assert label_id == 9
        assert event_at.hour == 19
        assert display == "05/08/2026 14:30"
