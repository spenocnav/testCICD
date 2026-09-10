"""tabla `usage_events`: auditoría de uso del portal por usuario

Revision ID: w9x0y1z20049
Revises: v8w9x0y10048
Create Date: 2026-08-31

Registra cada petición autenticada del API: quién (user_id), qué
(method + plantilla de ruta, nunca el path crudo con IDs), sobre qué flotas
(header X-Fleet-Id), cuándo, resultado y duración. Alimenta la pantalla
admin de auditoría de uso (/gestion/uso).

Decisiones:
- `user_id` NO es FK: la auditoría debe sobrevivir a un borrado de usuario.
  La pantalla resuelve email/nombre con un JOIN a `users` cuando existe.
- `route` guarda la PLANTILLA (`/api/v1/novedades/{novedad_id}`), no el path
  crudo: agrupa, y evita cardinalidad y PII (placas, UUIDs) en la tabla.
- `fleet_ids` es un array de UUID tal como llegó en X-Fleet-Id; NULL o vacío
  significa "todas las flotas del alcance", no ausencia de dato.
- Sin `created_at`/`updated_at`: `ts` es el instante del evento y las filas
  son inmutables (append-only, escritas en lote por el tracker).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "w9x0y1z20049"
down_revision: str | None = "v8w9x0y10048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "usage_events"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("route", sa.String(length=200), nullable=False),
        sa.Column("status_code", sa.SmallInteger(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("fleet_ids", postgresql.ARRAY(sa.UUID(as_uuid=True)), nullable=True),
        sa.CheckConstraint("duration_ms >= 0", name="ck_usage_events_duration_nonnegative"),
        sa.CheckConstraint(
            "status_code >= 100 AND status_code <= 599",
            name="ck_usage_events_status_range",
        ),
    )
    op.create_index(f"ix_{TABLE}_ts", TABLE, [sa.text("ts DESC")])
    op.create_index(f"ix_{TABLE}_user_ts", TABLE, ["user_id", sa.text("ts DESC")])


def downgrade() -> None:
    op.drop_index(f"ix_{TABLE}_user_ts", table_name=TABLE)
    op.drop_index(f"ix_{TABLE}_ts", table_name=TABLE)
    op.drop_table(TABLE)
