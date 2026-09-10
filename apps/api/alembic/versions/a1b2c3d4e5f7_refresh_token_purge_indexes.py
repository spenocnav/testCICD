"""índices mínimos para la purga de refresh tokens

Revision ID: a1b2c3d4e5f7
Revises: 1c2d3e4f5a60
Create Date: 2026-07-14

Housekeeping de la tabla ``refresh_tokens``: el purgado periódico
borra filas revocadas o expiradas más allá de un margen de retención
(ver ``app/services/refresh_token_purge_service.py``). La query es:

    DELETE FROM refresh_tokens
    WHERE id IN (
        SELECT id FROM refresh_tokens
        WHERE (revoked_at IS NOT NULL AND revoked_at < :cutoff)
           OR (expires_at < :cutoff)
        ORDER BY expires_at
        LIMIT :batch
    );

Para que esa query no haga sequential scan sobre la tabla, añadimos
DOS índices complementarios (mínimos, sólo los que la purga usa):

1. ``ix_refresh_tokens_expires_at`` sobre ``expires_at``: cubre la
   rama dominante (``expires_at < cutoff``) — todo refresh token
   emitido termina expirando tarde o temprano, así que esta rama
   representa la mayor parte del volumen a purgar con el tiempo.

2. ``ix_refresh_tokens_revoked_at_open`` parcial sobre
   ``(revoked_at) WHERE revoked_at IS NOT NULL``: cubre la rama de
   tokens revocados. La parcialidad es importante porque el grueso
   de la tabla son tokens vivos (revoked_at IS NULL), que no nos
   interesa escanear para esta query. Mantener el índice parcial
   pequeño hace que el purgado de revocados sea barato incluso si
   la tabla crece a millones de filas.

No tocamos ``ix_refresh_tokens_token_hash`` (ya UNIQUE; lo usa la
búsqueda por hash en login/refresh/logout) ni ``ix_refresh_tokens_user_id``
(lo usa la FK + cascadas + queries de "sesiones del usuario").

Downgrade: elimina ambos índices. La tabla sigue siendo funcional,
sólo vuelve a un patrón de scan lineal para la purga.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f7"
down_revision: str | None = "1c2d3e4f5a60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Cubre la rama "expirado" — la más frecuente a largo plazo.
    op.create_index(
        "ix_refresh_tokens_expires_at",
        "refresh_tokens",
        ["expires_at"],
    )

    # 2. Cubre la rama "revocado" sólo para filas con revoked_at no nulo.
    op.create_index(
        "ix_refresh_tokens_revoked_at_open",
        "refresh_tokens",
        ["revoked_at"],
        postgresql_where=sa.text("revoked_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_refresh_tokens_revoked_at_open", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_expires_at", table_name="refresh_tokens")
