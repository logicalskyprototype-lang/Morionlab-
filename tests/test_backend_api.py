from fastapi.testclient import TestClient

from backend.app import create_app

ADDON = {"Authorization": "Bearer addon-test-token"}
ADMIN = {"Authorization": "Bearer admin-test-token"}


def client_for(tmp_path, lease_seconds=60):
    database = "sqlite:///" + str(tmp_path / "motionlab-test.db")
    app = create_app(database_url=database, addon_token="addon-test-token",
                     admin_token="admin-test-token", lease_seconds=lease_seconds)
    return TestClient(app), app


def approved_job(**overrides):
    body = {
        "command": "create_cube",
        "args": {"name": "CI Cube", "size": 2, "location": [0, 0, 0]},
        "authorization": {"approved": True, "scopes": ["scene.write"]},
    }
    body.update(overrides)
    return body


def test_health_is_available(tmp_path):
    client, _ = client_for(tmp_path)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_addon_requires_bearer_token(tmp_path):
    client, _ = client_for(tmp_path)
    assert client.get("/v1/jobs/next").status_code == 401
    assert client.get("/v1/jobs/next", headers=ADDON).status_code == 200


def test_only_admin_can_enqueue_jobs(tmp_path):
    client, _ = client_for(tmp_path)
    assert client.post("/v1/admin/jobs", json=approved_job(), headers=ADDON).status_code == 401
    response = client.post("/v1/admin/jobs", json=approved_job(), headers=ADMIN)
    assert response.status_code == 201
    assert response.json()["status"] == "queued"


def test_job_requires_explicit_approval_and_scope(tmp_path):
    client, _ = client_for(tmp_path)
    no_approval = approved_job(authorization={"approved": False, "scopes": ["scene.write"]})
    no_scope = approved_job(authorization={"approved": True, "scopes": ["scene.read"]})
    assert client.post("/v1/admin/jobs", json=no_approval, headers=ADMIN).status_code == 422
    assert client.post("/v1/admin/jobs", json=no_scope, headers=ADMIN).status_code == 422


def test_command_and_arguments_are_allowlisted(tmp_path):
    client, _ = client_for(tmp_path)
    assert client.post("/v1/admin/jobs", json=approved_job(command="run_python"),
                       headers=ADMIN).status_code == 422
    unsafe = approved_job(args={"name": "Cube", "shell": "whoami"})
    assert client.post("/v1/admin/jobs", json=unsafe, headers=ADMIN).status_code == 422


def test_job_is_leased_and_result_is_idempotent(tmp_path):
    client, _ = client_for(tmp_path)
    created = client.post("/v1/admin/jobs", json=approved_job(), headers=ADMIN)
    job_id = created.json()["id"]
    leased = client.get("/v1/jobs/next", headers=ADDON).json()["job"]
    assert leased["id"] == job_id
    assert leased["authorization"]["approved"] is True

    client.post("/v1/admin/jobs", json=approved_job(), headers=ADMIN)
    assert client.get("/v1/jobs/next", headers=ADDON).json() == {"job": None}

    result = {"job_id": job_id, "status": "completed", "output": {"object_name": "CI Cube"}}
    first = client.post(f"/v1/jobs/{job_id}/result", json=result, headers=ADDON)
    second = client.post(f"/v1/jobs/{job_id}/result", json=result, headers=ADDON)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()


def test_result_cannot_be_submitted_for_unknown_job(tmp_path):
    client, _ = client_for(tmp_path)
    response = client.post("/v1/jobs/unknown/result",
                           json={"job_id": "unknown", "status": "completed"}, headers=ADDON)
    assert response.status_code == 404


def test_result_url_and_body_ids_must_match(tmp_path):
    client, _ = client_for(tmp_path)
    created = client.post("/v1/admin/jobs", json=approved_job(), headers=ADMIN)
    job_id = created.json()["id"]
    client.get("/v1/jobs/next", headers=ADDON)
    response = client.post(f"/v1/jobs/{job_id}/result",
                           json={"job_id": "different", "status": "completed"}, headers=ADDON)
    assert response.status_code == 400


def test_non_finite_numbers_are_rejected(tmp_path):
    client, _ = client_for(tmp_path)
    for invalid_number in ("NaN", "Infinity", "-Infinity"):
        body = (
            '{"command":"create_cube","args":{"size":' + invalid_number + '},'
            '"authorization":{"approved":true,"scopes":["scene.write"]}}'
        )
        response = client.post(
            "/v1/admin/jobs",
            content=body,
            headers={**ADMIN, "Content-Type": "application/json"},
        )
        assert response.status_code == 422


def test_failed_execution_is_persisted_as_failed_and_replayed(tmp_path):
    client, _ = client_for(tmp_path)
    created = client.post("/v1/admin/jobs", json=approved_job(), headers=ADMIN)
    job_id = created.json()["id"]
    client.get("/v1/jobs/next", headers=ADDON)

    result = {"job_id": job_id, "status": "failed", "error": "Blender operation failed"}
    first = client.post(f"/v1/jobs/{job_id}/result", json=result, headers=ADDON)
    replay = client.post(f"/v1/jobs/{job_id}/result", json=result, headers=ADDON)
    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == first.json()

    with client.app.state.session_factory() as session:
        from backend.app import Job
        stored = session.get(Job, job_id)
        assert stored.status == "failed"


def test_result_rejects_non_finite_values_and_oversized_output(tmp_path):
    client, _ = client_for(tmp_path)
    created = client.post("/v1/admin/jobs", json=approved_job(), headers=ADMIN)
    job_id = created.json()["id"]
    client.get("/v1/jobs/next", headers=ADDON)

    invalid_json = (
        '{"job_id":"' + job_id + '","status":"completed",'
        '"output":{"value":NaN}}'
    )
    response = client.post(
        f"/v1/jobs/{job_id}/result",
        content=invalid_json,
        headers={**ADDON, "Content-Type": "application/json"},
    )
    assert response.status_code == 422

    response = client.post(
        f"/v1/jobs/{job_id}/result",
        json={"job_id": job_id, "status": "completed", "output": {"value": "x" * (256 * 1024)}},
        headers=ADDON,
    )
    assert response.status_code == 422


def test_expired_lease_allows_job_to_be_released_again(tmp_path):
    from datetime import datetime, timedelta, timezone
    from backend.app import Job, WorkerLease

    client, app = client_for(tmp_path)
    created = client.post("/v1/admin/jobs", json=approved_job(), headers=ADMIN)
    job_id = created.json()["id"]
    first = client.get("/v1/jobs/next", headers=ADDON).json()["job"]
    assert first["id"] == job_id

    expired = datetime.now(timezone.utc) - timedelta(seconds=10)
    with app.state.session_factory() as session:
        job = session.get(Job, job_id)
        lease = session.get(WorkerLease, "default-installation")
        job.lease_expires_at = expired
        lease.lease_expires_at = expired
        session.commit()

    redelivered = client.get("/v1/jobs/next", headers=ADDON).json()["job"]
    assert redelivered["id"] == job_id

    with app.state.session_factory() as session:
        job = session.get(Job, job_id)
        assert job.attempts == 2


def test_result_retry_with_different_payload_is_conflict(tmp_path):
    client, _ = client_for(tmp_path)
    created = client.post("/v1/admin/jobs", json=approved_job(), headers=ADMIN)
    job_id = created.json()["id"]
    client.get("/v1/jobs/next", headers=ADDON)
    first = {"job_id": job_id, "status": "completed", "output": {"object_name": "Cube"}}
    changed = {"job_id": job_id, "status": "completed", "output": {"object_name": "Other"}}
    assert client.post(f"/v1/jobs/{job_id}/result", json=first, headers=ADDON).status_code == 200
    assert client.post(f"/v1/jobs/{job_id}/result", json=changed, headers=ADDON).status_code == 409


def test_expired_lease_cannot_acknowledge_job(tmp_path):
    from datetime import datetime, timedelta, timezone
    from backend.app import Job, WorkerLease

    client, app = client_for(tmp_path)
    created = client.post("/v1/admin/jobs", json=approved_job(), headers=ADMIN)
    job_id = created.json()["id"]
    client.get("/v1/jobs/next", headers=ADDON)
    expired = datetime.now(timezone.utc) - timedelta(seconds=10)
    with app.state.session_factory() as session:
        job = session.get(Job, job_id)
        lease = session.get(WorkerLease, "default-installation")
        job.lease_expires_at = expired
        lease.lease_expires_at = expired
        session.commit()
    response = client.post(
        f"/v1/jobs/{job_id}/result",
        json={"job_id": job_id, "status": "completed"},
        headers=ADDON,
    )
    assert response.status_code == 409



def test_extremely_large_numbers_are_rejected_without_server_error(tmp_path):
    client, _ = client_for(tmp_path)
    huge_integer = 10 ** 400
    for args in (
        {"size": huge_integer},
        {"location": [0, huge_integer, 0]},
    ):
        response = client.post(
            "/v1/admin/jobs",
            json=approved_job(args=args),
            headers=ADMIN,
        )
        assert response.status_code == 422
