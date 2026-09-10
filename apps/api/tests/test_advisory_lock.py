from __future__ import annotations

import pytest

from app.db.advisory_lock import (
    OperationAlreadyRunningError,
    advisory_lock_key,
    session_advisory_lock,
)
from app.db.session import engine


def test_advisory_lock_key_is_stable_and_namespaced() -> None:
    assert advisory_lock_key("master") == advisory_lock_key("master")
    assert advisory_lock_key("master") != advisory_lock_key("cloudfleet")
    assert -(2**63) <= advisory_lock_key("master") < 2**63


@pytest.mark.asyncio
async def test_session_advisory_lock_rejects_overlap_and_releases() -> None:
    name = "test:exclusive-operation"
    async with session_advisory_lock(engine, name):
        with pytest.raises(OperationAlreadyRunningError):
            async with session_advisory_lock(engine, name):
                pytest.fail("un segundo proceso no debe adquirir el mismo lock")

    async with session_advisory_lock(engine, name):
        pass
