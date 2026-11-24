"""Add persistent API rate-limit buckets and audit events.

Revision ID: 0002_rate_limits_audit_events
Revises: 0001_installation_job_schema
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_rate_limits_audit_events"
down_revision = "0001_installation_job_schema"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "motionlab_rate_limit_buckets",
        sa.Column("bucket_key", sa.String(length=64), primary_key=True),
        sa.Column("window_start_epoch", sa.Integer(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
    )
    op.create_table(
        "motionlab_audit_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_motionlab_audit_events_event_type", "motionlab_audit_events", ["event_type"])
    op.create_index("ix_motionlab_audit_events_target_id", "motionlab_audit_events", ["target_id"])
    op.create_index("ix_motionlab_audit_events_created_at", "motionlab_audit_events", ["created_at"])


def downgrade():
    # Preserve audit evidence and rate-limit state. Roll back by restoring a
    # reviewed database backup rather than dropping operational records.
    pass
