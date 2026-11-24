"""PostgreSQL-only integration tests for row-locking and lease behavior.

Run with MOTIONLAB_TEST_DATABASE_URL pointing at a disposable PostgreSQL database.
"""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app import Base, WorkerLease, create_app

DATABASE_URL = os.getenv("MOTIONLAB_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="PostgreSQL integration database not configured")

ADDON = {"Authorization": "Bearer addon-postgres-test-token"}
ADMIN = {"Authorization": "Bearer admin-postgres-test-token"}


@pytest.fixture()
def app():
    application = create_app(
        database_url=DATABASE_URL,
        addon_token="addon-postgres-test-token",
        admin_token="admin-postgres-test-token",
        lease_seconds=30,
    )
    # Each test gets an empty schema so queued jobs and the single worker lease
    # cannot leak between tests in the shared CI PostgreSQL service.
    Base.metadata.drop_all(application.state.engine)
    Base.metadata.create_all(application.state.engine)
    with application.state.session_factory() as session:
        session.add(WorkerLease(installation_id="default-installation"))
        session.commit()
    yield application
    application.state.engine.dispose()


def enqueue(client, name):
    response = client.post(
        "/v1/admin/jobs",
        headers=ADMIN,
        json={
            "command": "create_cube",
            "args": {"name": name, "size": 2, "location": [0, 0, 0]},
            "authorization": {"approved": True, "scopes": ["scene.write"]},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_concurrent_pollers_cannot_lease_two_jobs_to_one_installation(app):
    with TestClient(app) as client:
        first_id = enqueue(client, "Concurrent A " + uuid4().hex)
        second_id = enqueue(client, "Concurrent B " + uuid4().hex)

    workers = 12
    barrier = Barrier(workers)

    def poll():
        with TestClient(app) as client:
            barrier.wait(timeout=10)
            response = client.get("/v1/jobs/next", headers=ADDON)
            assert response.status_code == 200, response.text
            return response.json()["job"]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda _: poll(), range(workers)))

    leased = [job for job in results if job is not None]
    assert len(leased) == 1
    assert leased[0]["id"] in {first_id, second_id}

    with TestClient(app) as client:
        assert client.get("/v1/jobs/next", headers=ADDON).json() == {"job": None}


def test_postgres_lease_allows_next_job_after_result_is_accepted(app):
    with TestClient(app) as client:
        first_id = enqueue(client, "Postgres first " + uuid4().hex)
        second_id = enqueue(client, "Postgres second " + uuid4().hex)

        first = client.get("/v1/jobs/next", headers=ADDON).json()["job"]
        assert first["id"] == first_id

        accepted = client.post(
            f"/v1/jobs/{first_id}/result",
            headers=ADDON,
            json={"job_id": first_id, "status": "completed", "output": {"saved": True}},
        )
        assert accepted.status_code == 200, accepted.text

        second = client.get("/v1/jobs/next", headers=ADDON).json()["job"]
        assert second["id"] == second_id


def test_postgres_expired_lease_is_recovered(app):
    from datetime import datetime, timedelta, timezone

    from backend.app import Job, WorkerLease

    with TestClient(app) as client:
        job_id = enqueue(client, "Lease recovery " + uuid4().hex)
        first = client.get("/v1/jobs/next", headers=ADDON).json()["job"]
        assert first["id"] == job_id

        expired = datetime.now(timezone.utc) - timedelta(seconds=5)
        with app.state.session_factory() as session:
            job = session.get(Job, job_id)
            lease = session.get(WorkerLease, "default-installation")
            job.lease_expires_at = expired
            lease.lease_expires_at = expired
            session.commit()

        redelivered = client.get("/v1/jobs/next", headers=ADDON).json()["job"]
        assert redelivered["id"] == job_id

        with app.state.session_factory() as session:
            assert session.get(Job, job_id).attempts == 2
