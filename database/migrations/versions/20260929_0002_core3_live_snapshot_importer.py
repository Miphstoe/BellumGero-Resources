"""core3 live snapshot importer state

Revision ID: 20260929_0002
Revises: 20260928_0001
Create Date: 2026-09-29
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260929_0002"
down_revision: Union[str, None] = "20260928_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


BIGINT_ID = sa.BigInteger()
JSONB = postgresql.JSONB(astext_type=sa.Text())


def id_column() -> sa.Column:
    return sa.Column("id", BIGINT_ID, sa.Identity(), primary_key=True)


def jsonb_column(name: str) -> sa.Column:
    return sa.Column(name, JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"))


def upgrade() -> None:
    op.create_table(
        "core3_live_snapshot_imports",
        id_column(),
        sa.Column("source_instance_id", BIGINT_ID, sa.ForeignKey("source_instances.id"), nullable=False),
        sa.Column("source_snapshot_id", BIGINT_ID, sa.ForeignKey("source_snapshots.id"), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("advanced_current", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        jsonb_column("metadata"),
        sa.UniqueConstraint("source_snapshot_id", name="uq_core3_live_snapshot_imports_snapshot"),
        sa.UniqueConstraint("source_instance_id", "captured_at", name="uq_core3_live_snapshot_imports_instance_capture"),
    )
    op.create_index("ix_core3_live_snapshot_imports_instance_capture", "core3_live_snapshot_imports", ["source_instance_id", "captured_at"])
    op.create_index("ix_core3_live_snapshot_imports_instance_advanced", "core3_live_snapshot_imports", ["source_instance_id", "advanced_current"])

    op.create_table(
        "current_resource_availability",
        sa.Column("source_resource_id", BIGINT_ID, sa.ForeignKey("source_resources.id"), primary_key=True),
        sa.Column("source_instance_id", BIGINT_ID, sa.ForeignKey("source_instances.id"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_seen_source_snapshot_id", BIGINT_ID, sa.ForeignKey("source_snapshots.id")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("current_source_snapshot_id", BIGINT_ID, sa.ForeignKey("source_snapshots.id"), nullable=False),
        sa.Column("current_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absent_source_snapshot_id", BIGINT_ID, sa.ForeignKey("source_snapshots.id")),
        sa.Column("absent_as_of", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        jsonb_column("details"),
    )
    op.create_index("ix_current_resource_availability_instance_active", "current_resource_availability", ["source_instance_id", "is_active"])
    op.create_index("ix_current_resource_availability_current_snapshot", "current_resource_availability", ["current_source_snapshot_id"])


def downgrade() -> None:
    op.drop_index("ix_current_resource_availability_current_snapshot", table_name="current_resource_availability")
    op.drop_index("ix_current_resource_availability_instance_active", table_name="current_resource_availability")
    op.drop_table("current_resource_availability")
    op.drop_index("ix_core3_live_snapshot_imports_instance_advanced", table_name="core3_live_snapshot_imports")
    op.drop_index("ix_core3_live_snapshot_imports_instance_capture", table_name="core3_live_snapshot_imports")
    op.drop_table("core3_live_snapshot_imports")
