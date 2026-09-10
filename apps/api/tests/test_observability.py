from __future__ import annotations

from unittest.mock import Mock

from app.core import observability


def test_observability_is_noop_without_dsn(monkeypatch) -> None:
    monkeypatch.setattr(observability.settings, "environment", "development")
    monkeypatch.setattr(observability.settings, "sentry_dsn", None)

    observability.init_observability()


def test_observability_is_noop_in_test(monkeypatch) -> None:
    monkeypatch.setattr(observability.settings, "environment", "test")
    monkeypatch.setattr(observability.settings, "sentry_dsn", "https://public@example.invalid/1")

    observability.init_observability()


def test_observability_uses_typed_sampling_configuration(monkeypatch) -> None:
    import sentry_sdk

    init = Mock()
    monkeypatch.setattr(sentry_sdk, "init", init)
    monkeypatch.setattr(observability.settings, "environment", "staging")
    monkeypatch.setattr(observability.settings, "sentry_dsn", "https://public@example.invalid/1")
    monkeypatch.setattr(observability.settings, "sentry_traces_sample_rate", 0.125)

    observability.init_observability()

    kwargs = init.call_args.kwargs
    assert kwargs["environment"] == "staging"
    assert kwargs["traces_sample_rate"] == 0.125
    assert kwargs["send_default_pii"] is False
