"""Cifrado simétrico (Fernet) para secretos en reposo.

Se usa para las credenciales geotab de cada base de datos de cliente. La misma
`MASTER_FERNET_KEY` debe compartirse con InformesRendimiento, que descifra estas
credenciales al autenticarse contra geotab.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet

from app.core.config import settings


@lru_cache
def _fernet() -> Fernet:
    key = settings.master_fernet_key
    if not key:
        raise RuntimeError(
            "MASTER_FERNET_KEY no configurada; requerida para cifrar/descifrar "
            "credenciales geotab"
        )
    return Fernet(key.encode("utf-8") if isinstance(key, str) else key)


def encrypt_secret(plaintext: str) -> bytes:
    """Cifra un secreto en claro y devuelve el token Fernet (bytes)."""
    return _fernet().encrypt(plaintext.encode("utf-8"))


def decrypt_secret(token: bytes) -> str:
    """Descifra un token Fernet y devuelve el secreto en claro."""
    return _fernet().decrypt(bytes(token)).decode("utf-8")


def generate_key() -> str:
    """Genera una key Fernet nueva (base64 urlsafe). Útil para sembrar `.env`."""
    return Fernet.generate_key().decode("utf-8")
