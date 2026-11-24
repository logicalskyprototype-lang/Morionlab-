"""PostgreSQL-only integration tests for installation-scoped row locks and leases."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app import Base, Installation, WorkerLease, create_app

DATABASE_URL = os.getenv("MOTIONLAB_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="PostgreSQL integration database not configured")

ADMIN = {"Authorization": "Bearer admin-postgres-test-token"}


@pytest.fixture()
def app():
    application = create_app(
        database_url=DATABASE_URL,
        admin_token="admin-postgres-test-token",
        lease_seconds=30,
    )
    Base.metadata.drop_all(application.state.engine)
    Base.metadata.create_all(application.state.engine)
    yield application
    application.state.engine.dispose()


def make_installation(client, name):
    response = client.post("/v1/admin/installations", headers=ADMIN, json={"name": name})
    assert response.status_code == 201, response.text
    item = response.json()
    return item["installation_id"], {"Authorization": "Bearer " + item["access_token"]}


def enqueue(client, installation_id, name):
    response = client.post(
        "/v1/admin/jobs",
        headers=ADMIN,
        json={
            "installation_id": installation_id,
            "command": "create_cube",
            "args": {"name": name, "size": 2, "location": [0, 0, 0]},
            "authorization": {"approved": True, "scopes": ["scene.write"]},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_concurrent_pollers_cannot_lease_two_jobs_to_one_installation(app):
    with TestClient(app) as client:
        installation_id, addon = make_installation(client, "Concurrency worker")
        first_id = enqueue(client, installation_id, "Concurrent A " + uuid4().hex)
        second_id = enqueue(client, installation_id, "Concurrent B " + uuid4().hex)

    workers = 12
    barrier = Barrier(workers)

    def poll():
        with TestClient(app) as client:
            barrier.wait(timeout=10)
            response = client.get("/v1/jobs/next", headers=addon)
            assert response.status_code == 200, response.text
            return response.json()["job"]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda _: poll(), range(workers)))

    leased = [job for job in results if job is not None]
    assert len(leased) == 1
    assert leased[0]["id"] in {first_id, second_id}

    with TestClient(app) as client:
        assert client.get("/v1/jobs/next", headers=addon).json() == {"job": None}


def test_postgres_leases_are_isolated_across_installations(app):
    with TestClient(app) as client:
        first_id, first_addon = make_installation(client, "Worker one")
        second_id, second_addon = make_installation(client, "Worker two")
        first_job = enqueue(client, first_id, "First " + uuid4().hex)
        second_job = enqueue(client, second_id, "Second " + uuid4().hex)
        assert client.get("/v1/jobs/next", headers=first_addon).json()["job"]["id"] == first_job
        assert client.get("/v1/jobs/next", headers=second_addon).json()["job"]["id"] == second_job


def test_postgres_expired_lease_is_refenced_and_old_token_rejected(app):
    from datetime import datetime, timedelta, timezone
    from backend.app import Job, JobLeaseGrant, WorkerLease

    with TestClient(app) as client:
        installation_id, addon = make_installation(client, "Recovery worker")
        job_id = enqueue(client, installation_id, "Lease recovery " + uuid4().hex)
        first = client.get("/v1/jobs/next", headers=addon).json()["job"]
        assert first["id"] == job_id

        expired = datetime.now(timezone.utc) - timedelta(seconds=5)
        with app.state.session_factory() as session:
            job = session.get(Job, job_id)
            lease = session.get(WorkerLease, installation_id)
            grant = session.get(JobLeaseGrant, job_id)
            job.lease_expires_at = expired
            lease.lease_expires_at = expired
            grant.expires_at = expired
            session.commit()

        redelivered = client.get("/v1/jobs/next", headers=addon).json()["job"]
        assert redelivered["id"] == job_id
        assert redelivered["lease_token"] != first["lease_token"]

        stale = client.post(
            f"/v1/jobs/{job_id}/result",
            headers=addon,
            json={"job_id": job_id, "lease_token": first["lease_token"], "status": "completed"},
        )
        assert stale.status_code == 409

        accepted = client.post(
            f"/v1/jobs/{job_id}/result",
            headers=addon,
            json={"job_id": job_id, "lease_token": redelivered["lease_token"], "status": "completed"},
        )
        assert accepted.status_code == 200
