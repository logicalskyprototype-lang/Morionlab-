from fastapi.testclient import TestClient

from backend.app import Installation, Job, JobAssignment, JobLeaseGrant, WorkerLease, create_app

ADMIN = {"Authorization": "Bearer admin-test-token"}


def client_for(tmp_path, lease_seconds=60):
    database = "sqlite:///" + str(tmp_path / "motionlab-test.db")
    app = create_app(database_url=database, admin_token="admin-test-token",
                     lease_seconds=lease_seconds)
    client = TestClient(app)
    created = client.post("/v1/admin/installations", headers=ADMIN, json={"name": "Test Blender"})
    assert created.status_code == 201
    installation = created.json()
    addon = {"Authorization": "Bearer " + installation["access_token"]}
    return client, app, installation, addon


def approved_job(installation_id, **overrides):
    body = {
        "installation_id": installation_id,
        "command": "create_cube",
        "args": {"name": "CI Cube", "size": 2, "location": [0, 0, 0]},
        "authorization": {"approved": True, "scopes": ["scene.write"]},
    }
    body.update(overrides)
    return body


def enqueue(client, installation_id, **overrides):
    response = client.post("/v1/admin/jobs", json=approved_job(installation_id, **overrides), headers=ADMIN)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_health_is_available(tmp_path):
    client, _, _, _ = client_for(tmp_path)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_installation_credentials_are_one_time_and_revocable(tmp_path):
    client, app, installation, addon = client_for(tmp_path)
    assert client.get("/v1/jobs/next", headers=addon).status_code == 200
    with app.state.session_factory() as session:
        record = session.get(Installation, installation["installation_id"])
        assert record.token_hash != installation["access_token"]
        assert len(record.token_hash) == 64

    revoked = client.post(
        f"/v1/admin/installations/{installation['installation_id']}/revoke",
        headers=ADMIN,
    )
    assert revoked.status_code == 200
    assert client.get("/v1/jobs/next", headers=addon).status_code == 401


def test_only_admin_can_provision_and_enqueue(tmp_path):
    client, _, installation, addon = client_for(tmp_path)
    assert client.post("/v1/admin/installations", json={"name": "No"}, headers=addon).status_code == 401
    assert client.post("/v1/admin/jobs", json=approved_job(installation["installation_id"]), headers=addon).status_code == 401
    assert enqueue(client, installation["installation_id"])


def test_job_requires_explicit_approval_and_scope(tmp_path):
    client, _, installation, _ = client_for(tmp_path)
    no_approval = approved_job(installation["installation_id"], authorization={"approved": False, "scopes": ["scene.write"]})
    no_scope = approved_job(installation["installation_id"], authorization={"approved": True, "scopes": ["scene.read"]})
    assert client.post("/v1/admin/jobs", json=no_approval, headers=ADMIN).status_code == 422
    assert client.post("/v1/admin/jobs", json=no_scope, headers=ADMIN).status_code == 422


def test_command_and_arguments_are_allowlisted(tmp_path):
    client, _, installation, _ = client_for(tmp_path)
    assert client.post("/v1/admin/jobs", json=approved_job(installation["installation_id"], command="run_python"), headers=ADMIN).status_code == 422
    unsafe = approved_job(installation["installation_id"], args={"name": "Cube", "shell": "whoami"})
    assert client.post("/v1/admin/jobs", json=unsafe, headers=ADMIN).status_code == 422


def test_jobs_are_isolated_per_installation(tmp_path):
    client, _, first, first_addon = client_for(tmp_path)
    second_response = client.post("/v1/admin/installations", headers=ADMIN, json={"name": "Second Blender"})
    second = second_response.json()
    second_addon = {"Authorization": "Bearer " + second["access_token"]}
    first_job = enqueue(client, first["installation_id"], args={"name": "First"})
    second_job = enqueue(client, second["installation_id"], args={"name": "Second"})

    assert client.get("/v1/jobs/next", headers=first_addon).json()["job"]["id"] == first_job
    assert client.get("/v1/jobs/next", headers=second_addon).json()["job"]["id"] == second_job


def test_lease_token_is_required_and_fences_stale_results(tmp_path):
    client, app, installation, addon = client_for(tmp_path, lease_seconds=5)
    job_id = enqueue(client, installation["installation_id"])
    leased = client.get("/v1/jobs/next", headers=addon).json()["job"]
    assert leased["id"] == job_id
    assert leased["lease_token"]

    from datetime import datetime, timedelta, timezone
    expired = datetime.now(timezone.utc) - timedelta(seconds=10)
    with app.state.session_factory() as session:
        job = session.get(Job, job_id)
        lease = session.get(WorkerLease, installation["installation_id"])
        grant = session.get(JobLeaseGrant, job_id)
        job.lease_expires_at = expired
        lease.lease_expires_at = expired
        grant.expires_at = expired
        session.commit()

    redelivered = client.get("/v1/jobs/next", headers=addon).json()["job"]
    assert redelivered["id"] == job_id
    assert redelivered["lease_token"] != leased["lease_token"]

    stale = client.post(
        f"/v1/jobs/{job_id}/result",
        headers=addon,
        json={"job_id": job_id, "lease_token": leased["lease_token"], "status": "completed"},
    )
    assert stale.status_code == 409

    accepted_payload = {"job_id": job_id, "lease_token": redelivered["lease_token"], "status": "completed", "output": {"saved": True}}
    accepted = client.post(f"/v1/jobs/{job_id}/result", headers=addon, json=accepted_payload)
    replay = client.post(f"/v1/jobs/{job_id}/result", headers=addon, json=accepted_payload)
    assert accepted.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == accepted.json()
    with app.state.session_factory() as session:
        stored = session.get(Job, job_id)
        assert "lease_token" not in stored.result_json


def test_other_installation_cannot_submit_result(tmp_path):
    client, _, first, first_addon = client_for(tmp_path)
    second = client.post("/v1/admin/installations", headers=ADMIN, json={"name": "Other"}).json()
    second_addon = {"Authorization": "Bearer " + second["access_token"]}
    job_id = enqueue(client, first["installation_id"])
    leased = client.get("/v1/jobs/next", headers=first_addon).json()["job"]
    response = client.post(
        f"/v1/jobs/{job_id}/result",
        headers=second_addon,
        json={"job_id": job_id, "lease_token": leased["lease_token"], "status": "completed"},
    )
    assert response.status_code == 404


def test_expired_lease_cannot_acknowledge_job(tmp_path):
    from datetime import datetime, timedelta, timezone
    client, app, installation, addon = client_for(tmp_path)
    job_id = enqueue(client, installation["installation_id"])
    leased = client.get("/v1/jobs/next", headers=addon).json()["job"]
    expired = datetime.now(timezone.utc) - timedelta(seconds=10)
    with app.state.session_factory() as session:
        job = session.get(Job, job_id)
        lease = session.get(WorkerLease, installation["installation_id"])
        grant = session.get(JobLeaseGrant, job_id)
        job.lease_expires_at = expired
        lease.lease_expires_at = expired
        grant.expires_at = expired
        session.commit()
    response = client.post(
        f"/v1/jobs/{job_id}/result",
        headers=addon,
        json={"job_id": job_id, "lease_token": leased["lease_token"], "status": "completed"},
    )
    assert response.status_code == 409


def test_non_finite_numbers_are_rejected(tmp_path):
    client, _, installation, _ = client_for(tmp_path)
    for invalid_number in ("NaN", "Infinity", "-Infinity"):
        body = (
            '{"installation_id":"' + installation["installation_id"] + '",'
            '"command":"create_cube","args":{"size":' + invalid_number + '},'
            '"authorization":{"approved":true,"scopes":["scene.write"]}}'
        )
        response = client.post(
            "/v1/admin/jobs",
            content=body,
            headers={**ADMIN, "Content-Type": "application/json"},
        )
        assert response.status_code == 422


def test_revoking_installation_releases_its_pending_jobs(tmp_path):
    client, app, installation, addon = client_for(tmp_path)
    job_id = enqueue(client, installation["installation_id"])
    leased = client.get("/v1/jobs/next", headers=addon).json()["job"]
    assert leased["id"] == job_id

    response = client.post(f"/v1/admin/installations/{installation['installation_id']}/revoke", headers=ADMIN)
    assert response.status_code == 200
    with app.state.session_factory() as session:
        assert session.get(Job, job_id).status == "queued"
        assert session.get(JobAssignment, job_id) is None


def test_unknown_job_and_mismatched_result_ids_are_rejected(tmp_path):
    client, _, _, addon = client_for(tmp_path)
    unknown = client.post(
        "/v1/jobs/unknown/result",
        headers=addon,
        json={"job_id": "unknown", "lease_token": "unused", "status": "completed"},
    )
    assert unknown.status_code == 404

    job_id = enqueue(client, client.post(
        "/v1/admin/installations", headers=ADMIN, json={"name": "Mismatch worker"}
    ).json()["installation_id"])
    mismatch = client.post(
        f"/v1/jobs/{job_id}/result",
        headers=addon,
        json={"job_id": "different", "lease_token": "unused", "status": "completed"},
    )
    assert mismatch.status_code == 400


def test_result_rejects_non_finite_and_oversized_output(tmp_path):
    client, _, installation, addon = client_for(tmp_path)
    job_id = enqueue(client, installation["installation_id"])
    leased = client.get("/v1/jobs/next", headers=addon).json()["job"]

    invalid_json = (
        '{"job_id":"' + job_id + '","lease_token":"' + leased["lease_token"] + '",'
        '"status":"completed","output":{"value":NaN}}'
    )
    invalid = client.post(
        f"/v1/jobs/{job_id}/result",
        content=invalid_json,
        headers={**addon, "Content-Type": "application/json"},
    )
    assert invalid.status_code == 422

    oversized = client.post(
        f"/v1/jobs/{job_id}/result",
        headers=addon,
        json={
            "job_id": job_id,
            "lease_token": leased["lease_token"],
            "status": "completed",
            "output": {"value": "x" * (256 * 1024)},
        },
    )
    assert oversized.status_code == 422


def test_conflicting_result_retry_is_rejected(tmp_path):
    client, _, installation, addon = client_for(tmp_path)
    job_id = enqueue(client, installation["installation_id"])
    leased = client.get("/v1/jobs/next", headers=addon).json()["job"]
    first = {
        "job_id": job_id,
        "lease_token": leased["lease_token"],
        "status": "completed",
        "output": {"object_name": "Cube"},
    }
    changed = {
        "job_id": job_id,
        "lease_token": leased["lease_token"],
        "status": "completed",
        "output": {"object_name": "Different"},
    }
    assert client.post(f"/v1/jobs/{job_id}/result", headers=addon, json=first).status_code == 200
    assert client.post(f"/v1/jobs/{job_id}/result", headers=addon, json=changed).status_code == 409


def test_extremely_large_numbers_are_rejected_without_server_error(tmp_path):
    client, _, installation, _ = client_for(tmp_path)
    huge_integer = 10 ** 400
    for args in (
        {"size": huge_integer},
        {"location": [0, huge_integer, 0]},
    ):
        response = client.post(
            "/v1/admin/jobs",
            json=approved_job(installation["installation_id"], args=args),
            headers=ADMIN,
        )
        assert response.status_code == 422



def test_worker_heartbeat_sets_presence_and_renews_active_lease(tmp_path):
    from datetime import timedelta
    from backend.app import _utcnow

    client, app, _, _ = client_for(tmp_path)
    workspace = client.post(
        "/v1/admin/workspaces", headers=ADMIN, json={"name": "Heartbeat workspace"}
    ).json()
    workspace_headers = {"Authorization": "Bearer " + workspace["access_token"]}
    created = client.post(
        "/v1/workspaces/installations",
        headers=workspace_headers,
        json={"name": "Laptop Blender"},
    )
    assert created.status_code == 201
    installation = created.json()
    addon = {"Authorization": "Bearer " + installation["access_token"]}

    before = client.get("/v1/workspaces/installations", headers=workspace_headers).json()
    assert before["installations"][0]["status"] == "offline"

    idle_heartbeat = client.post("/v1/worker/heartbeat", headers=addon, json={})
    assert idle_heartbeat.status_code == 200
    assert idle_heartbeat.json()["lease_expires_at"] is None
    online = client.get("/v1/workspaces/installations", headers=workspace_headers).json()
    assert online["installations"][0]["status"] == "online"
    assert online["installations"][0]["last_seen_at"]

    queued = client.post(
        "/v1/workspaces/jobs",
        headers=workspace_headers,
        json=approved_job(installation["installation_id"]),
    )
    assert queued.status_code == 201
    job_id = queued.json()["id"]
    leased = client.get("/v1/jobs/next", headers=addon).json()["job"]

    old_expiry = _utcnow() + timedelta(seconds=10)
    with app.state.session_factory() as session:
        job = session.get(Job, job_id)
        worker_lease = session.get(WorkerLease, installation["installation_id"])
        grant = session.get(JobLeaseGrant, job_id)
        job.lease_expires_at = old_expiry
        worker_lease.lease_expires_at = old_expiry
        grant.expires_at = old_expiry
        session.commit()

    renewed = client.post(
        "/v1/worker/heartbeat",
        headers=addon,
        json={"job_id": job_id, "lease_token": leased["lease_token"]},
    )
    assert renewed.status_code == 200, renewed.text
    renewed_expiry = renewed.json()["lease_expires_at"]
    assert renewed_expiry

    with app.state.session_factory() as session:
        job = session.get(Job, job_id)
        worker_lease = session.get(WorkerLease, installation["installation_id"])
        grant = session.get(JobLeaseGrant, job_id)
        assert job.lease_expires_at == worker_lease.lease_expires_at
        assert worker_lease.lease_expires_at == grant.expires_at
        from backend.app import _is_future
        assert _is_future(worker_lease.lease_expires_at, old_expiry)


def test_worker_heartbeat_rejects_partial_or_stale_lease_credentials(tmp_path):
    client, _, installation, addon = client_for(tmp_path)
    partial = client.post(
        "/v1/worker/heartbeat",
        headers=addon,
        json={"job_id": "ml_missing"},
    )
    assert partial.status_code == 422

    job_id = enqueue(client, installation["installation_id"])
    leased = client.get("/v1/jobs/next", headers=addon).json()["job"]
    stale = client.post(
        "/v1/worker/heartbeat",
        headers=addon,
        json={"job_id": job_id, "lease_token": "wrong-token"},
    )
    assert stale.status_code == 409
