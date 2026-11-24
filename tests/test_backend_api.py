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
