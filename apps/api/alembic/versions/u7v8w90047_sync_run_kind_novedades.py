"""`sync_run.kind` admite `novedades`

Revision ID: u7v8w90047
Revises: t6u7v8w90046
Create Date: 2026-08-28

El paso 4 de la cadena diaria (réplica del estado real de las novedades desde
CloudFleet) audita en `sync_run` con `kind='novedades'`, y el CHECK
`ck_sync_run_kind` sólo admitía master/cloudfleet/reportes. Como la auditoría
es best-effort, el sync corría bien pero su fila se perdía en silencio con un
`sync_run_start_error` en el log. Se amplía el catálogo del CHECK.

El downgrade borra las filas `novedades` antes de restaurar el CHECK viejo:
sin eso, la restauración fallaría con datos ya escritos.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u7v8w90047"
down_revision: str | None = "t6u7v8w90046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "sync_run"
CONSTRAINT = "ck_sync_run_kind"
OLD_KINDS = ("master", "cloudfleet", "reportes")
NEW_KINDS = (*OLD_KINDS, "novedades")


def _kinds_sql(kinds: Sequence[str]) -> str:
    return "kind IN (" + ", ".join(f"'{kind}'" for kind in kinds) + ")"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    op.create_check_constraint(CONSTRAINT, TABLE, _kinds_sql(NEW_KINDS))


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM sync_run WHERE kind = 'novedades'"))
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    op.create_check_constraint(CONSTRAINT, TABLE, _kinds_sql(OLD_KINDS))
