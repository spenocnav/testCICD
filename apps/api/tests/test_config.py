"""Validación fail-fast de Settings en función del entorno.

Estos tests instancian ``Settings`` directamente con ``_env_file=None`` para
aislarlos del ``.env`` y de variables reales del entorno. Cada llamada pasa
explícitamente los campos relevantes (incluidos los opcionales) para evitar
contaminación entre tests y reflejos del proceso de pytest.
"""

from __future__ import annotations

from typing import Any

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.core.config import Settings

# Valores explícitamente seguros para los entornos que validan fail-fast.
SAFE_JWT_SECRET = "k7Qp2vZ9mX4tR8wL1nB6cF3hJ5sD0gA2eY7uI9oP4lK6"
SAFE_FERNET_KEY = Fernet.generate_key().decode()
SAFE_DATABASE_URL = (
    "postgresql+psycopg://portal_clientes:Zx9pQ2mL7vT4rW8n@postgres:5432/portal_clientes"
)


def test_default_database_driver_is_installed_runtime_driver() -> None:
    default_url = Settings.model_fields["database_url"].default
    assert isinstance(default_url, str)
    assert default_url.startswith("postgresql+psycopg://")


def _base_kwargs(environment: str) -> dict[str, Any]:
    """Devuelve kwargs que reproducen defaults conocidos sin tocar .env."""
    return {
        "environment": environment,
        # Secretos sobreescritos a valores explícitamente "seguros" para que
        # los tests dedicados a defaults inseguros fallen por su propia causa.
        "jwt_secret": SAFE_JWT_SECRET,
        "bootstrap_admin_password": "SafePass!2026",
        "bootstrap_admin_email": "admin@portalclientes.local",
        "minio_access_key": "safe-access",
        "minio_secret_key": "safe-secret",
        "master_fernet_key": SAFE_FERNET_KEY,
        "database_url": SAFE_DATABASE_URL,
        "navi_base_url": "https://vehiculos.example.test",
        "cloudfleet_base_url": "https://fleet.cloudfleet.com/api/v1",
        # CORS seguro por defecto; tests específicos lo sobreescriben.
        "cors_origins": "https://app.example.com",
        "cors_origin_regex": None,
        # Evitar reflejos desde el entorno del proceso.
        "database_url_local": None,
        "redis_url_local": None,
        "navi_api_key": None,
        "cloudfleet_api_key": None,
    }


def _settings(**overrides: Any) -> Settings:
    """Construye Settings aislados de .env y del entorno del proceso."""
    environment = overrides.pop("environment", "development")
    kwargs = _base_kwargs(environment)
    kwargs.update(overrides)
    return Settings(_env_file=None, **kwargs)


# ---------------------------------------------------------------------------
# Development / Test: defaults deben permitirse tal cual.


def test_development_keeps_known_defaults() -> None:
    """Development no debe activar ninguna validación fail-fast."""
    settings = Settings(
        _env_file=None,
        environment="development",
        jwt_secret="change-me-in-prod-32-chars-min-secret",
        bootstrap_admin_password="ChangeMe123!",
        minio_access_key="minioadmin",
        minio_secret_key="minioadmin",
    )
    assert settings.jwt_secret == "change-me-in-prod-32-chars-min-secret"
    assert settings.bootstrap_admin_password == "ChangeMe123!"
    assert settings.minio_access_key == "minioadmin"
    assert settings.minio_secret_key == "minioadmin"


def test_test_environment_keeps_known_defaults() -> None:
    """El entorno 'test' no debe activar la validación fail-fast."""
    settings = Settings(
        _env_file=None,
        environment="test",
        jwt_secret="change-me-in-prod-32-chars-min-secret",
        bootstrap_admin_password="ChangeMe123!",
        minio_access_key="minioadmin",
        minio_secret_key="minioadmin",
    )
    assert settings.jwt_secret == "change-me-in-prod-32-chars-min-secret"


# ---------------------------------------------------------------------------
# Staging: rechaza cada secreto default individualmente.


STAGING_INSECURE: dict[str, tuple[str, str]] = {
    "jwt_secret": ("jwt_secret", "change-me-in-prod-32-chars-min-secret"),
    "bootstrap_admin_password": ("bootstrap_admin_password", "ChangeMe123!"),
    "minio_access_key": ("minio_access_key", "minioadmin"),
    "minio_secret_key": ("minio_secret_key", "minioadmin"),
}


@pytest.mark.parametrize(
    ("field_name", "insecure_value"),
    list(STAGING_INSECURE.values()),
    ids=list(STAGING_INSECURE.keys()),
)
def test_staging_rejects_insecure_default_secret(field_name: str, insecure_value: str) -> None:
    """En staging, cada secreto default debe disparar ValidationError."""
    overrides: dict[str, Any] = {
        "environment": "staging",
        "cors_origins": "https://app.example.com",
    }
    # Dejar el resto de secretos en sus versiones seguras.
    overrides[field_name] = insecure_value

    with pytest.raises(ValidationError) as exc_info:
        _settings(**overrides)

    # El mensaje sólo menciona el nombre de la variable, nunca su valor.
    message = str(exc_info.value)
    assert field_name in message
    assert insecure_value not in message


def test_staging_accepts_explicit_safe_configuration() -> None:
    """Staging con secretos sobrescritos explícitamente debe pasar."""
    settings = _settings(
        environment="staging",
        cors_origins="https://app.example.com",
    )
    assert settings.environment == "staging"


# ---------------------------------------------------------------------------
# Production: CORS inseguro.


@pytest.mark.parametrize(
    "cors_origins",
    [
        "",  # lista vacía
        " * ",  # origen universal con espacios
        "*",  # origen universal sin espacios
        "http://localhost:3000",  # localhost
        "HTTP://LOCALHOST:3000",  # localhost en mayúsculas
        "http://127.0.0.1:8080",  # 127.0.0.1
        "https://app.example.com,http://localhost:3000",  # mezcla
    ],
    ids=[
        "empty",
        "wildcard-spaces",
        "wildcard",
        "localhost",
        "localhost-uppercase",
        "loopback-ip",
        "mixed-with-localhost",
    ],
)
def test_production_rejects_insecure_cors_origins(cors_origins: str) -> None:
    """Production debe rechazar CORS vacío, '*' y orígenes loopback."""
    with pytest.raises(ValidationError) as exc_info:
        _settings(environment="production", cors_origins=cors_origins)

    message = str(exc_info.value)
    assert "cors_origins" in message
    assert "production" in message


@pytest.mark.parametrize(
    "regex",
    [
        r".*",
        r"^.*$",
        r"  .*  ",
    ],
    ids=[
        "raw-dotstar",
        "anchored",
        "with-spaces",
    ],
)
def test_production_rejects_universal_cors_regex(regex: str) -> None:
    """Production debe rechazar regex universal '.*' (con anclas/espacios)."""
    with pytest.raises(ValidationError) as exc_info:
        _settings(
            environment="production",
            cors_origins="https://app.example.com",
            cors_origin_regex=regex,
        )

    message = str(exc_info.value)
    assert "cors_origin_regex" in message


def test_production_accepts_explicit_safe_configuration() -> None:
    """Una configuración production explícitamente segura debe aceptarse."""
    settings = _settings(
        environment="production",
        cors_origins="https://app.example.com,https://admin.example.com",
        cors_origin_regex=r"^https://.*\.example\.com$",
        jwt_secret=SAFE_JWT_SECRET,
        bootstrap_admin_password="SafePass!2026",
        minio_access_key="safe-access",
        minio_secret_key="safe-secret",
    )

    assert settings.environment == "production"
    assert settings.cors_origins_list == [
        "https://app.example.com",
        "https://admin.example.com",
    ]
    assert settings.cors_origin_regex == r"^https://.*\.example\.com$"


def test_production_accepts_literal_dot_star_regex() -> None:
    settings = _settings(environment="production", cors_origin_regex=r"\.\*")
    assert settings.cors_origin_regex == r"\.\*"


# ---------------------------------------------------------------------------
# Mensajes: nunca deben filtrar valores de los secretos.


def test_validation_messages_never_leak_secret_values() -> None:
    """Asegura que los mensajes sólo contienen nombres de variables."""
    secret_value = "change-me-in-prod-32-chars-min-secret"
    safe_bootstrap_password = "SafePass!2026"

    with pytest.raises(ValidationError) as exc_info:
        _settings(
            environment="staging",
            jwt_secret=secret_value,
            bootstrap_admin_password=safe_bootstrap_password,
            cors_origins="https://app.example.com",
        )

    message = str(exc_info.value)
    assert "jwt_secret" in message
    assert secret_value not in message
    assert safe_bootstrap_password not in message


# ---------------------------------------------------------------------------
# SEC-005: placeholders de plantilla y valores débiles.


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("jwt_secret", "REPLACE_WITH_AT_LEAST_32_RANDOM_CHARACTERS"),
        ("jwt_secret", ""),
        ("jwt_secret", "corto-pero-variado-123"),
        ("jwt_secret", "x" * 40),
        ("bootstrap_admin_password", "REPLACE_WITH_STRONG_ADMIN_PASSWORD"),
        ("bootstrap_admin_password", "Corta1!"),
        ("bootstrap_admin_email", "admin@example.com"),
        ("minio_access_key", "REPLACE_WITH_MINIO_ACCESS_KEY"),
        ("minio_secret_key", "<pon-aqui-el-secreto>"),
        ("master_fernet_key", None),
        ("master_fernet_key", "REPLACE_WITH_FERNET_KEY"),
        ("master_fernet_key", "no-es-una-key-fernet"),
        (
            "database_url",
            "postgresql+psycopg://portal_clientes:REPLACE_WITH_DATABASE_PASSWORD@postgres:5432/pc",
        ),
        (
            "database_url",
            "postgresql+psycopg://portal_clientes:portal_clientes_dev@postgres:5432/portal_clientes",
        ),
        ("navi_api_key", "REPLACE_WITH_NAVI_KEY"),
        ("cloudfleet_api_key", "changeme"),
    ],
    ids=[
        "jwt-placeholder",
        "jwt-empty",
        "jwt-short",
        "jwt-low-entropy",
        "bootstrap-placeholder",
        "bootstrap-short",
        "bootstrap-email-example",
        "minio-access-placeholder",
        "minio-secret-angle-brackets",
        "fernet-missing",
        "fernet-placeholder",
        "fernet-invalid",
        "db-password-placeholder",
        "db-password-dev",
        "navi-key-placeholder",
        "cloudfleet-key-placeholder",
    ],
)
@pytest.mark.parametrize("environment", ["staging", "production"])
def test_secure_environments_reject_placeholders(
    environment: str, field_name: str, value: str | None
) -> None:
    """La plantilla `deploy/production.env.example` sin editar no debe arrancar."""
    with pytest.raises(ValidationError) as exc_info:
        _settings(environment=environment, **{field_name: value})

    message = str(exc_info.value)
    assert field_name in message
    if value:
        assert value not in message


def test_production_template_as_is_is_rejected() -> None:
    """Los valores literales de la plantilla de producción fallan en bloque."""
    with pytest.raises(ValidationError) as exc_info:
        _settings(
            environment="production",
            database_url=(
                "postgresql+psycopg://portal_clientes:REPLACE_WITH_DATABASE_PASSWORD"
                "@postgres:5432/portal_clientes"
            ),
            jwt_secret="REPLACE_WITH_AT_LEAST_32_RANDOM_CHARACTERS",
            bootstrap_admin_email="admin@example.com",
            bootstrap_admin_password="REPLACE_WITH_STRONG_ADMIN_PASSWORD",
            minio_access_key="REPLACE_WITH_MINIO_ACCESS_KEY",
            minio_secret_key="REPLACE_WITH_MINIO_SECRET_KEY",
            master_fernet_key="REPLACE_WITH_FERNET_KEY",
        )
    message = str(exc_info.value)
    for name in (
        "database_url",
        "jwt_secret",
        "bootstrap_admin_email",
        "bootstrap_admin_password",
        "minio_access_key",
        "minio_secret_key",
        "master_fernet_key",
    ):
        assert name in message
    assert "REPLACE_WITH" not in message


def test_optional_integration_keys_may_be_absent_in_production() -> None:
    settings = _settings(environment="production", navi_api_key=None, cloudfleet_api_key=None)
    assert settings.navi_api_key is None


def test_development_accepts_placeholders_and_http() -> None:
    """Development no valida: el .env.example debe seguir arrancando tal cual."""
    settings = Settings(
        _env_file=None,
        environment="development",
        master_fernet_key=None,
        navi_base_url="http://localhost:8000",
    )
    assert settings.master_fernet_key is None


# ---------------------------------------------------------------------------
# SEC-032: integraciones sólo por HTTPS en entornos seguros.


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("navi_base_url", "http://192.168.11.108:8091"),
        ("navi_base_url", "http://vehiculos.proyectosnavi.dev"),
        ("cloudfleet_base_url", "http://fleet.cloudfleet.com/api/v1"),
        ("navi_base_url", "vehiculos.proyectosnavi.dev"),
    ],
    ids=["navi-lan-http", "navi-domain-http", "cloudfleet-http", "navi-sin-esquema"],
)
@pytest.mark.parametrize("environment", ["staging", "production"])
def test_secure_environments_require_https_integrations(
    environment: str, field_name: str, value: str
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        _settings(environment=environment, **{field_name: value})
    assert field_name in str(exc_info.value)


def test_https_integrations_are_accepted() -> None:
    settings = _settings(
        environment="production",
        navi_base_url="HTTPS://vehiculos.proyectosnavi.dev",
        cloudfleet_base_url="https://fleet.cloudfleet.com/api/v1",
    )
    assert settings.navi_base_url.lower().startswith("https://")
