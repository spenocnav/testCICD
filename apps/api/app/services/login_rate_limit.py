"""Límite distribuido de intentos fallidos de inicio de sesión por cuenta."""

from __future__ import annotations

from hashlib import sha256

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("login_rate_limit")

MAX_FAILURES = 5
WINDOW_SECONDS = 60

_CHECK_SCRIPT = """
local attempts = tonumber(redis.call('GET', KEYS[1]) or '0')
if attempts < tonumber(ARGV[1]) then return -1 end
local ttl = redis.call('TTL', KEYS[1])
if ttl < 1 then return tonumber(ARGV[2]) end
return ttl
"""

_FAILURE_SCRIPT = """
local attempts = redis.call('INCR', KEYS[1])
if attempts == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return attempts
"""

_client = Redis.from_url(
    settings.effective_redis_url,
    decode_responses=True,
    socket_connect_timeout=1.0,
    socket_timeout=1.0,
)


class LoginRateLimitError(Exception):
    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__("Demasiados intentos. Intente nuevamente más tarde.")


def _key(email: str) -> str:
    digest = sha256(email.strip().lower().encode("utf-8")).hexdigest()
    return f"portal-clientes:auth:login-failures:{digest}"


async def assert_login_allowed(email: str) -> None:
    if settings.environment == "test":
        return
    try:
        retry_after = int(
            await _client.eval(
                _CHECK_SCRIPT,
                1,
                _key(email),
                MAX_FAILURES,
                WINDOW_SECONDS,
            )
        )
    except (RedisError, OSError, ValueError, TypeError):
        # El límite grueso de SlowAPI conserva una defensa local si Redis cae;
        # una falla del limitador no debe bloquear a todos los clientes.
        logger.warning("login_rate_limit_check_failed")
        return
    if retry_after >= 0:
        raise LoginRateLimitError(retry_after=max(retry_after, 1))


async def record_login_failure(email: str) -> None:
    if settings.environment == "test":
        return
    try:
        await _client.eval(
            _FAILURE_SCRIPT,
            1,
            _key(email),
            WINDOW_SECONDS,
        )
    except (RedisError, OSError):
        logger.warning("login_rate_limit_record_failed")


async def clear_login_failures(email: str) -> None:
    if settings.environment == "test":
        return
    try:
        await _client.delete(_key(email))
    except (RedisError, OSError):
        logger.warning("login_rate_limit_clear_failed")
