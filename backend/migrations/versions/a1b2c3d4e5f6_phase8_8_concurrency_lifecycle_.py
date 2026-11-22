"""phase8_8_concurrency_lifecycle_metering

- appointments.block_start/block_end + PostgreSQL exclusion constraint
  (btree_gist + tstzrange) preventing overlapping bookings per rep.
- messages provider lifecycle columns (Resend/Meta correlation).
- message_templates provider metadata columns.
- lead_stage_history.metadata JSON.
- worker_heartbeats host/pid/started_at/job/error columns.
- llm_invocations table (actual provider-call metering).

Revision ID: a1b2c3d4e5f6
Revises: d295fbab8c8d
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'a1b2c3d4e5f6'
down_revision = 'd295fbab8c8d'
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == 'postgresql'

    # ---- appointments: occupied interval incl. buffer
    op.add_column('appointments', sa.Column('block_start', sa.DateTime(timezone=True), nullable=True))
    op.add_column('appointments', sa.Column('block_end', sa.DateTime(timezone=True), nullable=True))
    # backfill: use raw start/end (buffer applied by app going forward)
    op.execute("UPDATE appointments SET block_start = start_at, block_end = end_at "
               "WHERE block_start IS NULL")
    if is_pg:
        op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
        op.execute("""
            ALTER TABLE appointments
            ADD CONSTRAINT excl_appointment_overlap
            EXCLUDE USING gist (
                workspace_id WITH =,
                assigned_to WITH =,
                tstzrange(block_start, block_end, '[)') WITH &&
            ) WHERE (status::text IN
                ('proposed','awaiting_confirmation','confirmed','rescheduling_requested')
                AND block_start IS NOT NULL AND block_end IS NOT NULL)
        """)

    # ---- messages: provider lifecycle
    op.add_column('messages', sa.Column('provider_message_id', sa.String(length=300), nullable=True))
    op.add_column('messages', sa.Column('provider_delivered_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('messages', sa.Column('provider_read_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('messages', sa.Column('provider_failed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('messages', sa.Column('provider_error', sa.String(length=300), nullable=True))
    op.create_index('ix_messages_provider_id', 'messages', ['provider_message_id'])

    # ---- message_templates: provider metadata
    op.add_column('message_templates', sa.Column('provider_category', sa.String(length=50), nullable=True))
    op.add_column('message_templates', sa.Column('components', sa.JSON().with_variant(JSONB, 'postgresql'),
                                                 server_default='{}', nullable=False))
    op.add_column('message_templates', sa.Column('rejection_reason', sa.Text(), nullable=True))

    # ---- lead_stage_history: metadata
    op.add_column('lead_stage_history',
                  sa.Column('metadata', sa.JSON().with_variant(JSONB, 'postgresql'),
                            server_default='{}', nullable=False))

    # ---- workflows: atomic LLM budget counter
    op.add_column('workflows', sa.Column('llm_calls_used', sa.Integer(),
                                         server_default='0', nullable=False))

    # ---- worker_heartbeats: richer identity
    op.add_column('worker_heartbeats', sa.Column('host', sa.String(length=200), nullable=True))
    op.add_column('worker_heartbeats', sa.Column('pid', sa.Integer(), nullable=True))
    op.add_column('worker_heartbeats', sa.Column('started_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('worker_heartbeats', sa.Column('last_job_name', sa.String(length=200), nullable=True))
    op.add_column('worker_heartbeats', sa.Column('last_error', sa.String(length=300), nullable=True))

    # ---- llm_invocations
    op.create_table(
        'llm_invocations',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('workspace_id', sa.Uuid(), nullable=True),
        sa.Column('workflow_id', sa.Uuid(), nullable=True),
        sa.Column('step_id', sa.Uuid(), nullable=True),
        sa.Column('agent_id', sa.Uuid(), nullable=True),
        sa.Column('agent_slug', sa.String(length=100), nullable=True),
        sa.Column('provider', sa.String(length=50), nullable=False),
        sa.Column('model', sa.String(length=120), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('tokens_prompt', sa.Integer(), nullable=False),
        sa.Column('tokens_completion', sa.Integer(), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('error', sa.String(length=300), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['step_id'], ['workflow_steps.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['workflow_id'], ['workflows.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_llm_inv_workspace', 'llm_invocations', ['workspace_id', 'created_at'])
    op.create_index('ix_llm_inv_workflow', 'llm_invocations', ['workflow_id'])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute("ALTER TABLE appointments DROP CONSTRAINT IF EXISTS excl_appointment_overlap")
    op.drop_index('ix_llm_inv_workflow', table_name='llm_invocations')
    op.drop_index('ix_llm_inv_workspace', table_name='llm_invocations')
    op.drop_table('llm_invocations')
    op.drop_column('workflows', 'llm_calls_used')
    op.drop_column('worker_heartbeats', 'last_error')
    op.drop_column('worker_heartbeats', 'last_job_name')
    op.drop_column('worker_heartbeats', 'started_at')
    op.drop_column('worker_heartbeats', 'pid')
    op.drop_column('worker_heartbeats', 'host')
    op.drop_column('lead_stage_history', 'metadata')
    op.drop_column('message_templates', 'rejection_reason')
    op.drop_column('message_templates', 'components')
    op.drop_column('message_templates', 'provider_category')
    op.drop_index('ix_messages_provider_id', table_name='messages')
    op.drop_column('messages', 'provider_error')
    op.drop_column('messages', 'provider_failed_at')
    op.drop_column('messages', 'provider_read_at')
    op.drop_column('messages', 'provider_delivered_at')
    op.drop_column('messages', 'provider_message_id')
    op.drop_column('appointments', 'block_end')
    op.drop_column('appointments', 'block_start')
