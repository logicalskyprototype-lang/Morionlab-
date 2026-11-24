"""Add persistent API rate-limit buckets and audit events.

Revision ID: 0002_rate_limits_audit_events
Revises: 0001_installation_job_schema
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0002_rate_limits_audit_events"
down_revision = "0001_installation_job_schema"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    existing = set(inspector.get_table_names())

    expected_columns = {
        "motionlab_rate_limit_buckets": {
            "bucket_key", "window_start_epoch", "request_count",
        },
        "motionlab_audit_events": {
            "id", "event_type", "actor_type", "actor_id", "target_type",
            "target_id", "details_json", "created_at",
        },
    }
    for table_name, required in expected_columns.items():
        if table_name in existing:
            actual = {column["name"] for column in inspector.get_columns(table_name)}
            missing = required - actual
            if missing:
                raise RuntimeError(
                    f"Cannot adopt {table_name}: missing required columns "
                    f"{sorted(missing)}. Use a reviewed migration for this schema."
                )

    if "motionlab_rate_limit_buckets" not in existing:
        op.create_table(
            "motionlab_rate_limit_buckets",
            sa.Column("bucket_key", sa.String(length=64), primary_key=True),
            sa.Column("window_start_epoch", sa.Integer(), nullable=False),
            sa.Column("request_count", sa.Integer(), nullable=False),
        )
    if "motionlab_audit_events" not in existing:
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

    inspector = inspect(bind)
    indexes = {item["name"] for item in inspector.get_indexes("motionlab_audit_events")}
    if "ix_motionlab_audit_events_event_type" not in indexes:
        op.create_index("ix_motionlab_audit_events_event_type", "motionlab_audit_events", ["event_type"])
    if "ix_motionlab_audit_events_target_id" not in indexes:
        op.create_index("ix_motionlab_audit_events_target_id", "motionlab_audit_events", ["target_id"])
    if "ix_motionlab_audit_events_created_at" not in indexes:
        op.create_index("ix_motionlab_audit_events_created_at", "motionlab_audit_events", ["created_at"])


def downgrade():
    # Preserve audit evidence and rate-limit state. Roll back by restoring a
    # reviewed database backup rather than dropping operational records.
    pass
