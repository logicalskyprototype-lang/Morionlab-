"""Add MotionLab human accounts, sessions and magic-link challenges.

Revision ID: 0005_public_identity
Revises: 0004_workspace_projects
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_public_identity"
down_revision = "0004_workspace_projects"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "motionlab_users",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("email", sa.String(length=254), nullable=False, unique=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "motionlab_workspace_memberships",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("motionlab_users.id"), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), sa.ForeignKey("motionlab_workspaces.id"), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "workspace_id", name="uq_motionlab_membership_user_workspace"),
    )
    op.create_index(
        "ix_motionlab_workspace_memberships_user_status",
        "motionlab_workspace_memberships",
        ["user_id", "status"],
    )
    op.create_index(
        "ix_motionlab_workspace_memberships_workspace_id",
        "motionlab_workspace_memberships",
        ["workspace_id"],
    )
    op.create_table(
        "motionlab_browser_sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("motionlab_users.id"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_motionlab_browser_sessions_user_id", "motionlab_browser_sessions", ["user_id"])
    op.create_table(
        "motionlab_magic_link_challenges",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_motionlab_magic_link_email_created",
        "motionlab_magic_link_challenges",
        ["email", "created_at"],
    )
    op.create_table(
        "motionlab_auth_rate_limit_buckets",
        sa.Column("bucket_key", sa.String(length=64), primary_key=True),
        sa.Column("window_start_epoch", sa.Integer(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
    )


def downgrade():
    raise RuntimeError(
        "Identity data downgrade is disabled. Restore from a reviewed database backup."
    )
