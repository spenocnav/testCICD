"""preserve repeated Navifault link and asset placements

Revision ID: g3h4i5j60033
Revises: f2g3h4i50032
Create Date: 2026-08-24

QuickServe pages can link to the same document or image several times.  The
placement order is part of that official context, so it belongs in the
identity of the relationship instead of being overwritten by an upsert.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "g3h4i5j60033"
down_revision: str | None = "f2g3h4i50032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "navifault"

PLACEMENT_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "fault_analysis_documents": ("analysis_id", "document_id", "sort_order"),
    "fault_page_documents": ("fault_page_id", "document_id", "sort_order"),
    "technical_document_links": ("parent_document_id", "child_document_id", "sort_order"),
    "fault_page_assets": ("fault_page_id", "asset_id", "ordinal"),
    "fault_analysis_assets": ("analysis_id", "asset_id", "ordinal"),
    "technical_document_assets": ("document_id", "asset_id", "ordinal"),
}


def _replace_primary_key(table_name: str, columns: tuple[str, ...]) -> None:
    op.drop_constraint(f"{table_name}_pkey", table_name, schema=SCHEMA, type_="primary")
    op.create_primary_key(f"{table_name}_pkey", table_name, columns, schema=SCHEMA)


def upgrade() -> None:
    for table_name, columns in PLACEMENT_PRIMARY_KEYS.items():
        _replace_primary_key(table_name, columns)


def downgrade() -> None:
    # El downgrade sólo es válido si no existen múltiples placements del mismo
    # par padre-hijo.  En una base con el corpus importado no se debe bajar.
    previous_primary_keys: dict[str, tuple[str, ...]] = {
        "fault_analysis_documents": ("analysis_id", "document_id"),
        "fault_page_documents": ("fault_page_id", "document_id"),
        "technical_document_links": ("parent_document_id", "child_document_id"),
        "fault_page_assets": ("fault_page_id", "asset_id"),
        "fault_analysis_assets": ("analysis_id", "asset_id"),
        "technical_document_assets": ("document_id", "asset_id"),
    }
    for table_name, columns in previous_primary_keys.items():
        _replace_primary_key(table_name, columns)
