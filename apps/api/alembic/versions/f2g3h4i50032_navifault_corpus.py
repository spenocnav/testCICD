"""create private navifault corpus schema

Revision ID: f2g3h4i50032
Revises: e1f2g3h40031
Create Date: 2026-08-23

The technical Cummins corpus is operational Portal data. Its metadata belongs
to the private ``navifault`` schema while the official HTML and binaries live
in a private MinIO bucket. ``analytics`` remains ETL-owned and untouched.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f2g3h4i50032"
down_revision: str | None = "e1f2g3h40031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "navifault"
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    op.create_table(
        "corpus_import_runs",
        sa.Column("import_id", sa.String(length=96), nullable=False),
        sa.Column("corpus_version", sa.String(length=96), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("source_manifest", JSONB, nullable=False),
        sa.Column("audit", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("import_id"),
        schema=SCHEMA,
    )

    op.create_table(
        "manuals",
        sa.Column("pub_id", sa.String(length=96), nullable=False),
        sa.Column("language", sa.String(length=2), nullable=False),
        sa.Column("engine_models", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("fault_pages_total", sa.Integer(), nullable=False),
        sa.Column("source_status", sa.String(length=24), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("language IN ('ES', 'EN')", name="ck_navifault_manual_language"),
        sa.CheckConstraint(
            "source_status IN ('ready', 'incomplete')", name="ck_navifault_manual_status"
        ),
        sa.PrimaryKeyConstraint("pub_id"),
        schema=SCHEMA,
    )

    op.create_table(
        "engine_manual_candidates",
        sa.Column("engine_serial", sa.String(length=64), nullable=False),
        sa.Column("pub_id", sa.String(length=96), nullable=False),
        sa.Column("cpl", sa.String(length=64), nullable=True),
        sa.Column("candidate_type", sa.String(length=16), nullable=False),
        sa.Column("source_record", JSONB, nullable=False),
        sa.CheckConstraint(
            "candidate_type IN ('primary', 'alternate')",
            name="ck_navifault_engine_candidate_type",
        ),
        sa.ForeignKeyConstraint(["pub_id"], [f"{SCHEMA}.manuals.pub_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("engine_serial", "pub_id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_navifault_engine_candidate_pub",
        "engine_manual_candidates",
        ["pub_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "fault_pages",
        sa.Column("fault_page_id", sa.String(length=96), nullable=False),
        sa.Column("pub_id", sa.String(length=96), nullable=False),
        sa.Column("language", sa.String(length=2), nullable=False),
        sa.Column("engine_model", sa.String(length=512), nullable=True),
        sa.Column("fault_code", sa.Integer(), nullable=False),
        sa.Column("variant", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("raw_html_object_key", sa.Text(), nullable=True),
        sa.Column("raw_html_sha256", sa.String(length=64), nullable=True),
        sa.Column("raw_html_status", sa.String(length=16), nullable=False),
        sa.Column("summary", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("sections", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("source_record", JSONB, nullable=False),
        sa.CheckConstraint("language IN ('ES', 'EN')", name="ck_navifault_fault_page_language"),
        sa.CheckConstraint(
            "raw_html_status IN ('available', 'missing')", name="ck_navifault_page_html"
        ),
        sa.ForeignKeyConstraint(["pub_id"], [f"{SCHEMA}.manuals.pub_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("fault_page_id"),
        sa.UniqueConstraint(
            "pub_id", "fault_code", "variant", "source_url", name="uq_navifault_page_source"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_navifault_fault_pages_pub_code", "fault_pages", ["pub_id", "fault_code"], schema=SCHEMA
    )

    op.create_table(
        "fault_protocol_keys",
        sa.Column("fault_protocol_key_id", sa.String(length=96), nullable=False),
        sa.Column("fault_page_id", sa.String(length=96), nullable=False),
        sa.Column("protocol", sa.String(length=8), nullable=False),
        sa.Column("namespace", sa.String(length=8), nullable=False),
        sa.Column("diagnostic_code", sa.Integer(), nullable=False),
        sa.Column("fmi", sa.Integer(), nullable=True),
        sa.Column("match_specificity", sa.String(length=16), nullable=False),
        sa.Column("source_codes", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "protocol IN ('J1939', 'J1708')", name="ck_navifault_protocol_key_protocol"
        ),
        sa.CheckConstraint(
            "namespace IN ('SPN', 'PID', 'SID')", name="ck_navifault_protocol_key_namespace"
        ),
        sa.CheckConstraint(
            "match_specificity IN ('code_fmi', 'code_only')",
            name="ck_navifault_protocol_key_specificity",
        ),
        sa.ForeignKeyConstraint(
            ["fault_page_id"], [f"{SCHEMA}.fault_pages.fault_page_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("fault_protocol_key_id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_navifault_protocol_lookup",
        "fault_protocol_keys",
        ["protocol", "namespace", "diagnostic_code", "fmi"],
        schema=SCHEMA,
    )

    op.create_table(
        "fault_page_tables",
        sa.Column("fault_page_id", sa.String(length=96), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("raw_table_html", sa.Text(), nullable=False),
        sa.Column("table_data", JSONB, nullable=False),
        sa.ForeignKeyConstraint(
            ["fault_page_id"], [f"{SCHEMA}.fault_pages.fault_page_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("fault_page_id", "ordinal"),
        schema=SCHEMA,
    )

    op.create_table(
        "fault_analyses",
        sa.Column("analysis_id", sa.String(length=96), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("raw_html_object_key", sa.Text(), nullable=True),
        sa.Column("raw_html_sha256", sa.String(length=64), nullable=True),
        sa.Column("raw_html_status", sa.String(length=16), nullable=False),
        sa.Column("steps", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("source_analysis", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.CheckConstraint(
            "raw_html_status IN ('available', 'missing')", name="ck_navifault_analysis_html"
        ),
        sa.PrimaryKeyConstraint("analysis_id"),
        sa.UniqueConstraint("source_url", name="uq_navifault_analysis_source_url"),
        schema=SCHEMA,
    )

    op.create_table(
        "fault_page_analyses",
        sa.Column("fault_page_id", sa.String(length=96), nullable=False),
        sa.Column("analysis_id", sa.String(length=96), nullable=False),
        sa.ForeignKeyConstraint(
            ["analysis_id"], [f"{SCHEMA}.fault_analyses.analysis_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["fault_page_id"], [f"{SCHEMA}.fault_pages.fault_page_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("fault_page_id", "analysis_id"),
        schema=SCHEMA,
    )
    op.create_table(
        "fault_analysis_tables",
        sa.Column("analysis_id", sa.String(length=96), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("raw_table_html", sa.Text(), nullable=False),
        sa.Column("table_data", JSONB, nullable=False),
        sa.ForeignKeyConstraint(
            ["analysis_id"], [f"{SCHEMA}.fault_analyses.analysis_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("analysis_id", "ordinal"),
        schema=SCHEMA,
    )

    op.create_table(
        "technical_documents",
        sa.Column("document_id", sa.String(length=96), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("document_type", sa.String(length=64), nullable=True),
        sa.Column("raw_html_object_key", sa.Text(), nullable=True),
        sa.Column("raw_html_sha256", sa.String(length=64), nullable=True),
        sa.Column("raw_html_status", sa.String(length=16), nullable=False),
        sa.Column("text_content", sa.Text(), nullable=True),
        sa.Column("sections", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("steps", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("warnings", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("cautions", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("source_document", JSONB, nullable=False),
        sa.CheckConstraint(
            "raw_html_status IN ('available', 'missing')", name="ck_navifault_document_html"
        ),
        sa.PrimaryKeyConstraint("document_id"),
        sa.UniqueConstraint("source_url", name="uq_navifault_document_source_url"),
        schema=SCHEMA,
    )
    op.create_table(
        "technical_document_tables",
        sa.Column("document_id", sa.String(length=96), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("raw_table_html", sa.Text(), nullable=False),
        sa.Column("table_data", JSONB, nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"], [f"{SCHEMA}.technical_documents.document_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("document_id", "ordinal"),
        schema=SCHEMA,
    )

    op.create_table(
        "assets",
        sa.Column("asset_id", sa.String(length=96), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("object_key", sa.Text(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("media_type", sa.String(length=128), nullable=True),
        sa.Column("local_status", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "local_status IN ('available', 'missing')", name="ck_navifault_asset_status"
        ),
        sa.PrimaryKeyConstraint("asset_id"),
        sa.UniqueConstraint("source_url", name="uq_navifault_asset_source_url"),
        schema=SCHEMA,
    )

    op.create_table(
        "fault_analysis_documents",
        sa.Column("analysis_id", sa.String(length=96), nullable=False),
        sa.Column("document_id", sa.String(length=96), nullable=False),
        sa.Column("link_type", sa.String(length=64), nullable=True),
        sa.Column("link_text", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["analysis_id"], [f"{SCHEMA}.fault_analyses.analysis_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], [f"{SCHEMA}.technical_documents.document_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("analysis_id", "document_id"),
        schema=SCHEMA,
    )
    op.create_table(
        "fault_page_documents",
        sa.Column("fault_page_id", sa.String(length=96), nullable=False),
        sa.Column("document_id", sa.String(length=96), nullable=False),
        sa.Column("link_type", sa.String(length=64), nullable=True),
        sa.Column("link_text", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("link_origin", sa.String(length=24), nullable=False),
        sa.CheckConstraint(
            "link_origin IN ('fc_record', 'analysis_discovery')",
            name="ck_navifault_page_document_origin",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], [f"{SCHEMA}.technical_documents.document_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["fault_page_id"], [f"{SCHEMA}.fault_pages.fault_page_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("fault_page_id", "document_id"),
        schema=SCHEMA,
    )
    op.create_table(
        "technical_document_links",
        sa.Column("parent_document_id", sa.String(length=96), nullable=False),
        sa.Column("child_document_id", sa.String(length=96), nullable=False),
        sa.Column("link_type", sa.String(length=64), nullable=True),
        sa.Column("link_text", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["child_document_id"], [f"{SCHEMA}.technical_documents.document_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["parent_document_id"],
            [f"{SCHEMA}.technical_documents.document_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("parent_document_id", "child_document_id"),
        schema=SCHEMA,
    )

    op.create_table(
        "fault_page_assets",
        sa.Column("fault_page_id", sa.String(length=96), nullable=False),
        sa.Column("asset_id", sa.String(length=96), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("display_url", sa.Text(), nullable=True),
        sa.Column("high_res_url", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["asset_id"], [f"{SCHEMA}.assets.asset_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["fault_page_id"], [f"{SCHEMA}.fault_pages.fault_page_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("fault_page_id", "asset_id"),
        schema=SCHEMA,
    )
    op.create_table(
        "fault_analysis_assets",
        sa.Column("analysis_id", sa.String(length=96), nullable=False),
        sa.Column("asset_id", sa.String(length=96), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("display_url", sa.Text(), nullable=True),
        sa.Column("high_res_url", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["analysis_id"], [f"{SCHEMA}.fault_analyses.analysis_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["asset_id"], [f"{SCHEMA}.assets.asset_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("analysis_id", "asset_id"),
        schema=SCHEMA,
    )
    op.create_table(
        "technical_document_assets",
        sa.Column("document_id", sa.String(length=96), nullable=False),
        sa.Column("asset_id", sa.String(length=96), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("display_url", sa.Text(), nullable=True),
        sa.Column("high_res_url", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["asset_id"], [f"{SCHEMA}.assets.asset_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["document_id"], [f"{SCHEMA}.technical_documents.document_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("document_id", "asset_id"),
        schema=SCHEMA,
    )

    op.create_table(
        "dateplate_manual_map",
        sa.Column("normalized_service_model_name", sa.String(length=512), nullable=False),
        sa.Column("pub_id", sa.String(length=96), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("mapping_status", sa.String(length=24), nullable=False),
        sa.Column("mapping_evidence", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint(
            "mapping_status IN ('verified', 'pending', 'retired')",
            name="ck_navifault_dateplate_status",
        ),
        sa.ForeignKeyConstraint(["pub_id"], [f"{SCHEMA}.manuals.pub_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("normalized_service_model_name", "pub_id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_navifault_dateplate_lookup",
        "dateplate_manual_map",
        ["normalized_service_model_name", "priority"],
        schema=SCHEMA,
    )

    op.create_table(
        "fault_page_language_links",
        sa.Column("source_fault_page_id", sa.String(length=96), nullable=False),
        sa.Column("target_fault_page_id", sa.String(length=96), nullable=False),
        sa.Column("evidence", JSONB, nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_navifault_language_confidence"
        ),
        sa.CheckConstraint(
            "status IN ('candidate', 'verified', 'rejected')", name="ck_navifault_language_status"
        ),
        sa.ForeignKeyConstraint(
            ["source_fault_page_id"], [f"{SCHEMA}.fault_pages.fault_page_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["target_fault_page_id"], [f"{SCHEMA}.fault_pages.fault_page_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("source_fault_page_id", "target_fault_page_id"),
        schema=SCHEMA,
    )

    op.create_table(
        "generated_technical_descriptions",
        sa.Column("description_id", sa.String(length=96), nullable=False),
        sa.Column("fault_page_id", sa.String(length=96), nullable=False),
        sa.Column("context_sha256", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=96), nullable=False),
        sa.Column("model_version", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "generation_metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed')",
            name="ck_navifault_description_status",
        ),
        sa.ForeignKeyConstraint(
            ["fault_page_id"], [f"{SCHEMA}.fault_pages.fault_page_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("description_id"),
        sa.UniqueConstraint(
            "fault_page_id",
            "context_sha256",
            "prompt_version",
            "model_version",
            name="uq_navifault_description_cache",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_navifault_description_status",
        "generated_technical_descriptions",
        ["status", "updated_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_navifault_description_status",
        table_name="generated_technical_descriptions",
        schema=SCHEMA,
    )
    op.drop_table("generated_technical_descriptions", schema=SCHEMA)
    op.drop_table("fault_page_language_links", schema=SCHEMA)
    op.drop_index("ix_navifault_dateplate_lookup", table_name="dateplate_manual_map", schema=SCHEMA)
    op.drop_table("dateplate_manual_map", schema=SCHEMA)
    op.drop_table("technical_document_assets", schema=SCHEMA)
    op.drop_table("fault_analysis_assets", schema=SCHEMA)
    op.drop_table("fault_page_assets", schema=SCHEMA)
    op.drop_table("technical_document_links", schema=SCHEMA)
    op.drop_table("fault_page_documents", schema=SCHEMA)
    op.drop_table("fault_analysis_documents", schema=SCHEMA)
    op.drop_table("assets", schema=SCHEMA)
    op.drop_table("technical_document_tables", schema=SCHEMA)
    op.drop_table("technical_documents", schema=SCHEMA)
    op.drop_table("fault_analysis_tables", schema=SCHEMA)
    op.drop_table("fault_page_analyses", schema=SCHEMA)
    op.drop_table("fault_analyses", schema=SCHEMA)
    op.drop_table("fault_page_tables", schema=SCHEMA)
    op.drop_index("ix_navifault_protocol_lookup", table_name="fault_protocol_keys", schema=SCHEMA)
    op.drop_table("fault_protocol_keys", schema=SCHEMA)
    op.drop_index("ix_navifault_fault_pages_pub_code", table_name="fault_pages", schema=SCHEMA)
    op.drop_table("fault_pages", schema=SCHEMA)
    op.drop_index(
        "ix_navifault_engine_candidate_pub", table_name="engine_manual_candidates", schema=SCHEMA
    )
    op.drop_table("engine_manual_candidates", schema=SCHEMA)
    op.drop_table("manuals", schema=SCHEMA)
    op.drop_table("corpus_import_runs", schema=SCHEMA)
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}")
