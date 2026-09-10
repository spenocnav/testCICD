from app.core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)


def test_password_roundtrip() -> None:
    hashed = hash_password("ChangeMe123!")
    assert hashed != "ChangeMe123!"
    assert verify_password("ChangeMe123!", hashed)
    assert not verify_password("wrong", hashed)


def test_password_long_input_truncated() -> None:
    long_pwd = "x" * 200
    hashed = hash_password(long_pwd)
    assert verify_password(long_pwd, hashed)
    assert verify_password("x" * 72, hashed)


def test_access_token_roundtrip() -> None:
    token = create_access_token("user-123", extra_claims={"role": "admin"})
    payload = decode_token(token, expected_type="access")
    assert payload["sub"] == "user-123"
    assert payload["role"] == "admin"
    assert payload["type"] == "access"


def test_refresh_token_type_mismatch() -> None:
    token = create_refresh_token("user-123")
    payload = decode_token(token, expected_type="refresh")
    assert payload["type"] == "refresh"

    import pytest

    with pytest.raises(TokenError):
        decode_token(token, expected_type="access")


def test_invalid_token_raises() -> None:
    import pytest

    with pytest.raises(TokenError):
        decode_token("not-a-token")


def test_refresh_token_hash_deterministic() -> None:
    token = create_refresh_token("user-123")
    assert hash_refresh_token(token) == hash_refresh_token(token)
    assert len(hash_refresh_token(token)) == 64  # sha256 hex


async def test_password_async_matches_sync() -> None:
    from app.core.security import hash_password_async, verify_password_async

    hashed = await hash_password_async("ChangeMe123!")
    assert await verify_password_async("ChangeMe123!", hashed)
    assert not await verify_password_async("wrong", hashed)
    # El hash async debe ser verificable por el camino síncrono (migraciones,
    # scripts) y viceversa: es el mismo algoritmo, solo cambia dónde corre.
    assert verify_password("ChangeMe123!", hashed)
    assert await verify_password_async("ChangeMe123!", hash_password("ChangeMe123!"))


async def test_password_verification_does_not_block_event_loop() -> None:
    """Regresión de PERF-002: bcrypt no debe detener el event loop.

    Con las llamadas síncronas dentro de la corrutina, cuatro verificaciones
    consecutivas medían ~2,1 s de event-loop lag. El heartbeat de abajo lo
    detecta: si bcrypt vuelve al loop, el gap máximo se dispara.
    """
    import asyncio
    import time

    from app.core.security import hash_password_async, verify_password_async

    hashed = await hash_password_async("ChangeMe123!")

    max_gap = 0.0
    stop = asyncio.Event()

    async def heartbeat() -> None:
        nonlocal max_gap
        last = time.perf_counter()
        while not stop.is_set():
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            max_gap = max(max_gap, now - last)
            last = now

    beat = asyncio.create_task(heartbeat())
    try:
        results = await asyncio.gather(
            *(verify_password_async("ChangeMe123!", hashed) for _ in range(4))
        )
    finally:
        stop.set()
        await beat

    assert all(results)
    # Umbral holgado para CI lenta; el modo bloqueante daba ~2 s.
    assert max_gap < 0.25, f"event loop bloqueado {max_gap:.3f}s durante bcrypt"
