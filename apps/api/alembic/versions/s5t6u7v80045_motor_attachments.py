"""réplica de los adjuntos del motor (curvas de par y potencia)

Revision ID: s5t6u7v80045
Revises: r4s5t6u70044
Create Date: 2026-08-28

Navi Vehículos guarda un PDF de curva por motor y CPL. El snapshot maestro ya
trae los metadatos (`motors[].attachments`); esta tabla es su réplica local.

El binario NO se replica en el sync. Son ~200 KB por documento y el snapshot se
pide completo en cada incremental: embeberlos multiplicaría por veinte el peso
de una sincronización que corre cada día para no traer nada nuevo. La descarga
es perezosa —el primer acceso la dispara— y queda cacheada en MinIO, así que un
documento que nadie abre nunca se transfiere.

Las tres columnas de huella (`source_stored_filename`, `source_updated_at`,
`file_size`) son la detección de cambio. `source_stored_filename` es el nombre
del objeto en el almacenamiento de Navi, un uuid4 nuevo por cada carga: si
cambia, el binario cambió, y no hace falta descargarlo para saberlo. Al
detectarlo el sync invalida la caché y el siguiente acceso vuelve a descargar.

Tabla nueva y aditiva: no toca ninguna consulta existente.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "s5t6u7v80045"
down_revision: str | None = "r4s5t6u70044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "motor_attachments"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("motor_type", sa.String(), nullable=False),
        sa.Column("cpl", sa.String(), nullable=True),
        sa.Column("original_filename", sa.String(), nullable=True),
        sa.Column("content_type", sa.String(), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("source_stored_filename", sa.String(), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("object_key", sa.String(), nullable=True),
        sa.Column("content_sha256", sa.String(), nullable=True),
        sa.Column(
            "fetch_status",
            sa.String(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("fetch_error", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["motor_type"],
            ["motor_catalog.motor_type"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("source_id", name="uq_motor_attachment_source_id"),
        sa.CheckConstraint(
            "fetch_status IN ('pending', 'ready', 'failed')",
            name="ck_motor_attachment_fetch_status",
        ),
    )
    op.create_index(f"ix_{TABLE}_motor_type", TABLE, ["motor_type"])
    op.create_index(
        f"ix_{TABLE}_motor_type_active",
        TABLE,
        ["motor_type", "is_active"],
    )


def downgrade() -> None:
    op.drop_index(f"ix_{TABLE}_motor_type_active", table_name=TABLE)
    op.drop_index(f"ix_{TABLE}_motor_type", table_name=TABLE)
    op.drop_table(TABLE)
