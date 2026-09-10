"""Integración aislada para rotación y purga de refresh tokens.

Estas pruebas nunca usan la base de desarrollo. Sólo se habilitan cuando
``TEST_AUTH_DATABASE_URL`` nombra exactamente ``portal_clientes_codex_test``.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services.auth_service import AuthError, issue_refresh_token, rotate_refresh_token
from app.services.refresh_token_purge_service import count_purgeable, purge_once

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
async def auth_session_factory():
    raw_url = os.environ.get("TEST_AUTH_DATABASE_URL")
    if not raw_url or make_url(raw_url).database != "portal_clientes_codex_test":
        pytest.skip(
            "TEST_AUTH_DATABASE_URL debe apuntar exactamente a portal_clientes_codex_test"
        )

    engine = create_async_engine(raw_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _create_user(factory) -> User:
    async with factory() as db:
        user = User(
            email=f"auth-hardening-{uuid.uuid4()}@example.test",
            password_hash="not-used-by-this-test",
            full_name="Auth hardening test",
            is_active=True,
        )
        db.add(user)
        await db.commit()
        return user


@pytest.mark.asyncio
async def test_same_refresh_token_can_only_rotate_once(auth_session_factory) -> None:
    user = await _create_user(auth_session_factory)
    try:
        async with auth_session_factory() as db:
            stored_user = await db.get(User, user.id)
            assert stored_user is not None
            raw_token = await issue_refresh_token(db, stored_user)
            await db.commit()

        async def rotate_once() -> bool:
            async with auth_session_factory() as db:
                try:
                    await rotate_refresh_token(db, raw_token)
                    await db.commit()
                    return True
                except AuthError:
                    await db.rollback()
                    return False

        results = await asyncio.gather(rotate_once(), rotate_once())
        assert sorted(results) == [False, True]

        async with auth_session_factory() as db:
            tokens = (
                await db.execute(
                    select(RefreshToken).where(RefreshToken.user_id == user.id)
                )
            ).scalars().all()
            assert len(tokens) == 2
            assert sum(token.revoked_at is None for token in tokens) == 1
    finally:
        async with auth_session_factory() as db:
            await db.execute(delete(User).where(User.id == user.id))
            await db.commit()


@pytest.mark.asyncio
async def test_purge_is_batched_idempotent_and_keeps_recent_rows(
    auth_session_factory,
) -> None:
    user = await _create_user(auth_session_factory)
    now = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
    rows = [
        RefreshToken(
            user_id=user.id,
            token_hash=f"old-expired-{uuid.uuid4()}",
            expires_at=now - timedelta(days=30),
        ),
        RefreshToken(
            user_id=user.id,
            token_hash=f"old-revoked-{uuid.uuid4()}",
            expires_at=now + timedelta(days=30),
            revoked_at=now - timedelta(days=30),
        ),
        RefreshToken(
            user_id=user.id,
            token_hash=f"recent-expired-{uuid.uuid4()}",
            expires_at=now - timedelta(days=1),
        ),
        RefreshToken(
            user_id=user.id,
            token_hash=f"active-{uuid.uuid4()}",
            expires_at=now + timedelta(days=30),
        ),
    ]
    try:
        async with auth_session_factory() as db:
            db.add_all(rows)
            await db.commit()
            assert await count_purgeable(db, now=now) == 2

        async with auth_session_factory() as db:
            assert (await purge_once(db, now=now, batch_size=1)).deleted == 1
        async with auth_session_factory() as db:
            assert (await purge_once(db, now=now, batch_size=1)).deleted == 1
        async with auth_session_factory() as db:
            assert (await purge_once(db, now=now, batch_size=1)).deleted == 0

        async with auth_session_factory() as db:
            remaining = (
                await db.execute(
                    select(RefreshToken).where(RefreshToken.user_id == user.id)
                )
            ).scalars().all()
            assert {token.token_hash for token in remaining} == {
                rows[2].token_hash,
                rows[3].token_hash,
            }
    finally:
        async with auth_session_factory() as db:
            await db.execute(delete(User).where(User.id == user.id))
            await db.commit()
