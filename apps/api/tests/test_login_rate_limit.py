from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.services import login_rate_limit


@pytest.fixture(autouse=True)
def production_rate_limit(monkeypatch):
    monkeypatch.setattr(login_rate_limit.settings, "environment", "production")


@pytest.mark.asyncio
async def test_blocks_with_retry_after_when_failure_limit_is_reached(monkeypatch) -> None:
    monkeypatch.setattr(login_rate_limit._client, "eval", AsyncMock(return_value=27))

    with pytest.raises(login_rate_limit.LoginRateLimitError) as error:
        await login_rate_limit.assert_login_allowed("User@Example.com")

    assert error.value.retry_after == 27


@pytest.mark.asyncio
async def test_allows_account_below_failure_limit(monkeypatch) -> None:
    monkeypatch.setattr(login_rate_limit._client, "eval", AsyncMock(return_value=-1))

    await login_rate_limit.assert_login_allowed("user@example.com")


@pytest.mark.asyncio
async def test_account_key_is_normalized_and_does_not_expose_email(monkeypatch) -> None:
    evaluate = AsyncMock(return_value=1)
    monkeypatch.setattr(login_rate_limit._client, "eval", evaluate)

    await login_rate_limit.record_login_failure("  User@Example.com ")

    key = evaluate.await_args.args[2]
    assert "user@example.com" not in key
    assert key == login_rate_limit._key("user@example.com")


@pytest.mark.asyncio
async def test_successful_login_clears_previous_failures(monkeypatch) -> None:
    delete = AsyncMock()
    monkeypatch.setattr(login_rate_limit._client, "delete", delete)

    await login_rate_limit.clear_login_failures("user@example.com")

    delete.assert_awaited_once_with(login_rate_limit._key("user@example.com"))


@pytest.mark.asyncio
async def test_redis_outage_does_not_block_all_logins(monkeypatch) -> None:
    monkeypatch.setattr(
        login_rate_limit._client,
        "eval",
        AsyncMock(side_effect=RedisConnectionError("redis unavailable")),
    )

    await login_rate_limit.assert_login_allowed("user@example.com")
