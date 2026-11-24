from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from backend.app import Base


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
            "motionlab_jobs",
            "motionlab_installations",
            "motionlab_job_assignments",
            "motionlab_worker_leases",
            "motionlab_job_lease_grants",
            "motionlab_rate_limit_buckets",
            "motionlab_audit_events",
        }.issubset(tables)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0002_rate_limits_audit_events"
    finally:
        engine.dispose()


def test_migration_adopts_existing_create_all_schema_without_losing_rows(tmp_path):
    database_url = "sqlite:///" + str(tmp_path / "existing.db")
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO motionlab_installations "
                "(id, name, token_hash, created_at, revoked_at) "
                "VALUES ('ins_0123456789abcdef0123456789abcdef', 'Existing worker', :hash, CURRENT_TIMESTAMP, NULL)"
            ),
            {"hash": "a" * 64},
        )

    try:
        command.upgrade(_config(database_url), "head")
        with engine.connect() as connection:
            name = connection.execute(
                text("SELECT name FROM motionlab_installations WHERE id = 'ins_0123456789abcdef0123456789abcdef'")
            ).scalar_one()
            assert name == "Existing worker"
            assert connection.execute(text("SELECT COUNT(*) FROM motionlab_jobs")).scalar_one() == 0
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0002_rate_limits_audit_events"
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
