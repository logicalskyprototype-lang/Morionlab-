"""Create or adopt the MotionLab v0.2 installation/job schema.

Revision ID: 0001_installation_job_schema
Revises:
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0001_installation_job_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    existing = set(inspect(bind).get_table_names())

    # Never stamp an existing but structurally incompatible database as upgraded.
    expected_columns = {
        "motionlab_jobs": {
            "id", "command", "args_json", "authorization_json", "status",
            "lease_expires_at", "attempts", "result_json", "created_at",
        },
        "motionlab_installations": {
            "id", "name", "token_hash", "created_at", "revoked_at",
        },
        "motionlab_job_assignments": {"job_id", "installation_id"},
        "motionlab_worker_leases": {
            "installation_id", "active_job_id", "lease_expires_at",
        },
        "motionlab_job_lease_grants": {
            "job_id", "installation_id", "token_hash", "expires_at",
        },
    }
    inspector = inspect(bind)
    for table_name, required in expected_columns.items():
        if table_name in existing:
            actual = {column["name"] for column in inspector.get_columns(table_name)}
            missing = required - actual
            if missing:
                raise RuntimeError(
                    f"Cannot adopt {table_name}: missing required columns "
                    f"{sorted(missing)}. Use a reviewed migration for this schema."
                )

    if "motionlab_jobs" not in existing:
        op.create_table(
            "motionlab_jobs",
            sa.Column("id", sa.String(length=128), primary_key=True),
            sa.Column("command", sa.String(length=64), nullable=False),
            sa.Column("args_json", sa.Text(), nullable=False),
            sa.Column("authorization_json", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("result_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "motionlab_installations" not in existing:
        op.create_table(
            "motionlab_installations",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "motionlab_job_assignments" not in existing:
        op.create_table(
            "motionlab_job_assignments",
            sa.Column("job_id", sa.String(length=128), primary_key=True),
            sa.Column("installation_id", sa.String(length=36), nullable=False),
        )
    if "motionlab_worker_leases" not in existing:
        op.create_table(
            "motionlab_worker_leases",
            sa.Column("installation_id", sa.String(length=36), primary_key=True),
            sa.Column("active_job_id", sa.String(length=128), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "motionlab_job_lease_grants" not in existing:
        op.create_table(
            "motionlab_job_lease_grants",
            sa.Column("job_id", sa.String(length=128), primary_key=True),
            sa.Column("installation_id", sa.String(length=36), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        )

    # create_all() from earlier development releases created these indexes.
    # Add missing indexes when adopting an existing database.
    tables = {
        table: {item["name"] for item in inspect(bind).get_indexes(table)}
        for table in (
            "motionlab_jobs",
            "motionlab_installations",
            "motionlab_job_assignments",
            "motionlab_job_lease_grants",
        )
    }
    if "ix_motionlab_jobs_status" not in tables["motionlab_jobs"]:
        op.create_index("ix_motionlab_jobs_status", "motionlab_jobs", ["status"])
    if "ix_motionlab_installations_token_hash" not in tables["motionlab_installations"]:
        op.create_index("ix_motionlab_installations_token_hash", "motionlab_installations", ["token_hash"], unique=True)
    if "ix_motionlab_job_assignments_installation_id" not in tables["motionlab_job_assignments"]:
        op.create_index("ix_motionlab_job_assignments_installation_id", "motionlab_job_assignments", ["installation_id"])
    if "ix_motionlab_job_lease_grants_installation_id" not in tables["motionlab_job_lease_grants"]:
        op.create_index("ix_motionlab_job_lease_grants_installation_id", "motionlab_job_lease_grants", ["installation_id"])


def downgrade():
    # This is an adoption/baseline migration: it may run against a database
    # containing pre-existing production data. Destructive rollback is unsafe.
    pass
