"""phase8_9_index_name_alignment

Align the messages.provider_message_id index name with the model's
auto-generated name (ix_messages_provider_message_id). The earlier
migration created ix_messages_provider_id; alembic check flagged drift.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-11-22
"""
from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the old-named index if present, then create the model-expected name.
    # Both statements are idempotent-safe on fresh DBs where neither exists
    # only if we guard; SQLite supports DROP INDEX IF EXISTS.
    op.execute("DROP INDEX IF EXISTS ix_messages_provider_id")
    op.create_index("ix_messages_provider_message_id", "messages",
                    ["provider_message_id"])


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_messages_provider_message_id")
    op.create_index("ix_messages_provider_id", "messages",
                    ["provider_message_id"])
