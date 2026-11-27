from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _config(database_url):
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def test_migration_creates_fresh_schema_and_is_repeatable(tmp_path):
    database_url = "sqlite:///" + str(tmp_path / "fresh.db")
    config = _config(database_url)

    command.upgrade(config, "head")
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert {
            "alembic_version",
            "motionlab_workspaces",
            "motionlab_workspace_credentials",
            "motionlab_jobs",
            "motionlab_installations",
            "motionlab_job_assignments",
            "motionlab_worker_leases",
            "motionlab_job_lease_grants",
            "motionlab_rate_limit_buckets",
            "motionlab_audit_events",
            "motionlab_projects",
            "motionlab_users",
            "motionlab_workspace_memberships",
            "motionlab_browser_sessions",
            "motionlab_magic_link_challenges",
            "motionlab_auth_rate_limit_buckets",
        }.issubset(tables)
        assert "last_seen_at" in {
            column["name"] for column in inspect(engine).get_columns("motionlab_installations")
        }
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0006_worker_heartbeat"
            assert connection.execute(
                text("SELECT status FROM motionlab_workspaces WHERE id = 'wsp_legacy'")
            ).scalar_one() == "active"
    finally:
        engine.dispose()


def test_workspace_migration_adopts_existing_legacy_rows_without_losing_data(tmp_path):
    database_url = "sqlite:///" + str(tmp_path / "existing.db")
    config = _config(database_url)
    command.upgrade(config, "0002_rate_limits_audit_events")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO motionlab_installations "
                "(id, name, token_hash, created_at, revoked_at) "
                "VALUES ('ins_0123456789abcdef0123456789abcdef', 'Existing worker', :hash, CURRENT_TIMESTAMP, NULL)"
            ),
            {"hash": "a" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO motionlab_jobs "
                "(id, command, args_json, authorization_json, status, lease_expires_at, attempts, result_json, created_at) "
                "VALUES ('ml_existing', 'create_cube', '{}', :authorization, "
                "'queued', NULL, 0, NULL, CURRENT_TIMESTAMP)"
            ),
            {"authorization": '{"approved":true,"scopes":["scene.write"]}'},
        )

    try:
        command.upgrade(config, "head")
        with engine.connect() as connection:
            installation = connection.execute(
                text("SELECT name, workspace_id FROM motionlab_installations WHERE id = 'ins_0123456789abcdef0123456789abcdef'")
            ).one()
            assert installation.name == "Existing worker"
            assert installation.workspace_id == "wsp_legacy"
            job = connection.execute(
                text("SELECT workspace_id FROM motionlab_jobs WHERE id = 'ml_existing'")
            ).scalar_one()
            assert job == "wsp_legacy"
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0006_worker_heartbeat"
    finally:
        engine.dispose()


def test_migration_rejects_partial_incompatible_existing_schema(tmp_path):
    database_url = "sqlite:///" + str(tmp_path / "partial.db")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE motionlab_jobs (id VARCHAR(128) PRIMARY KEY, status VARCHAR(16))")
        )
    engine.dispose()

    import pytest
    with pytest.raises(RuntimeError, match="missing required columns"):
        command.upgrade(_config(database_url), "head")


def test_security_migration_rejects_partial_rate_limit_table(tmp_path):
    database_url = "sqlite:///" + str(tmp_path / "partial-rate-limit.db")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE motionlab_rate_limit_buckets (bucket_key VARCHAR(64) PRIMARY KEY)")
        )
    engine.dispose()

    import pytest
    with pytest.raises(RuntimeError, match="Cannot adopt motionlab_rate_limit_buckets"):
        command.upgrade(_config(database_url), "head")
