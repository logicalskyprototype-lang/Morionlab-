"""Add workspace tenancy and scoped credentials.

Revision ID: 0003_workspace_authorization
Revises: 0002_rate_limits_audit_events
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0003_workspace_authorization"
down_revision = "0002_rate_limits_audit_events"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    if "motionlab_workspaces" not in tables:
        op.create_table(
            "motionlab_workspaces",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    required_workspace_columns = {
        "motionlab_workspaces": {"id", "name", "status", "created_at"},
        "motionlab_workspace_credentials": {"id", "workspace_id", "token_hash", "created_at", "revoked_at"},
    }
    for table_name, required in required_workspace_columns.items():
        if table_name in tables:
            actual = {item["name"] for item in inspect(bind).get_columns(table_name)}
            missing = required - actual
            if missing:
                raise RuntimeError(
                    f"Cannot adopt {table_name}: missing required columns {sorted(missing)}."
                )

    if "motionlab_workspace_credentials" not in tables:
        op.create_table(
            "motionlab_workspace_credentials",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("workspace_id", sa.String(36), sa.ForeignKey("motionlab_workspaces.id"), nullable=False),
            sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        )
    credential_indexes = {item["name"] for item in inspect(bind).get_indexes("motionlab_workspace_credentials")}
    if "ix_motionlab_workspace_credentials_workspace_id" not in credential_indexes:
        op.create_index("ix_motionlab_workspace_credentials_workspace_id", "motionlab_workspace_credentials", ["workspace_id"])
    if "ix_motionlab_workspace_credentials_token_hash" not in credential_indexes:
        op.create_index("ix_motionlab_workspace_credentials_token_hash", "motionlab_workspace_credentials", ["token_hash"], unique=True)

    connection = op.get_bind()
    connection.execute(
        sa.text(
            "INSERT INTO motionlab_workspaces (id, name, status, created_at) "
            "SELECT 'wsp_legacy', 'Legacy workspace', 'active', CURRENT_TIMESTAMP "
            "WHERE NOT EXISTS (SELECT 1 FROM motionlab_workspaces WHERE id = 'wsp_legacy')"
        )
    )

    for table_name in ("motionlab_installations", "motionlab_jobs"):
        columns = {item["name"] for item in inspect(bind).get_columns(table_name)}
        if "workspace_id" not in columns:
            op.add_column(
                table_name,
                sa.Column("workspace_id", sa.String(36), nullable=True),
            )
        bind.execute(
            sa.text(f"UPDATE {table_name} SET workspace_id = 'wsp_legacy' WHERE workspace_id IS NULL")
        )
        # Batch mode works on SQLite and PostgreSQL and adds the ownership FK.
        foreign_keys = {tuple(item.get("constrained_columns", [])) for item in inspect(bind).get_foreign_keys(table_name)}
        has_workspace_fk = ("workspace_id",) in foreign_keys
        if not has_workspace_fk:
            with op.batch_alter_table(table_name) as batch:
                batch.alter_column("workspace_id", existing_type=sa.String(36), nullable=False)
                batch.create_foreign_key(
                    f"fk_{table_name}_workspace_id",
                    "motionlab_workspaces", ["workspace_id"], ["id"],
                )
        else:
            with op.batch_alter_table(table_name) as batch:
                batch.alter_column("workspace_id", existing_type=sa.String(36), nullable=False)

        indexes = {item["name"] for item in inspect(bind).get_indexes(table_name)}
        index_name = f"ix_{table_name}_workspace_id"
        if index_name not in indexes:
            op.create_index(index_name, table_name, ["workspace_id"])

    audit_columns = {item["name"] for item in inspect(bind).get_columns("motionlab_audit_events")}
    if "workspace_id" not in audit_columns:
        op.add_column("motionlab_audit_events", sa.Column("workspace_id", sa.String(36), nullable=True))
    audit_fks = {tuple(item.get("constrained_columns", [])) for item in inspect(bind).get_foreign_keys("motionlab_audit_events")}
    if ("workspace_id",) not in audit_fks:
        with op.batch_alter_table("motionlab_audit_events") as batch:
            batch.create_foreign_key(
                "fk_motionlab_audit_events_workspace_id",
                "motionlab_workspaces", ["workspace_id"], ["id"],
            )
    audit_indexes = {item["name"] for item in inspect(bind).get_indexes("motionlab_audit_events")}
    if "ix_motionlab_audit_events_workspace_id" not in audit_indexes:
        op.create_index("ix_motionlab_audit_events_workspace_id", "motionlab_audit_events", ["workspace_id"])


def downgrade():
    # Tenant ownership is security-critical. Use a reviewed database restore
    # rather than a destructive downgrade that could collapse tenant boundaries.
    pass
