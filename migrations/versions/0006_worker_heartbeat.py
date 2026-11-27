"""Add worker heartbeat timestamps for Blender installations.

Revision ID: 0006_worker_heartbeat
Revises: 0005_public_identity
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0006_worker_heartbeat"
down_revision = "0005_public_identity"
branch_labels = None
depends_on = None


def upgrade():
    inspector = inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("motionlab_installations")}
    if "last_seen_at" not in columns:
        op.add_column(
            "motionlab_installations",
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade():
    inspector = inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("motionlab_installations")}
    if "last_seen_at" in columns:
        op.drop_column("motionlab_installations", "last_seen_at")
