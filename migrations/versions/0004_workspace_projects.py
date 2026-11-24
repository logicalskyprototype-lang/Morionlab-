"""Add workspace-scoped creative projects.

Revision ID: 0004_workspace_projects
Revises: 0003_workspace_authorization
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_workspace_projects"
down_revision = "0003_workspace_authorization"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "motionlab_projects",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(length=36),
            sa.ForeignKey("motionlab_workspaces.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("project_type", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_motionlab_projects_workspace_created",
        "motionlab_projects",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "ix_motionlab_projects_status",
        "motionlab_projects",
        ["status"],
    )


def downgrade():
    # Projects may contain customer work. Do not make destructive downgrade
    # the default recovery path; restore from a reviewed database backup.
    raise RuntimeError(
        "Destructive project downgrade is disabled. Restore from a reviewed backup."
    )
