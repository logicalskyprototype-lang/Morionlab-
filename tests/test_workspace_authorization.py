from fastapi.testclient import TestClient

from backend.app import Installation, WorkspaceCredential, _token_hash, create_app

ADMIN = {"Authorization": "Bearer admin-workspace-test"}


def setup_client(tmp_path):
    app = create_app(
        database_url="sqlite:///" + str(tmp_path / "workspace.db"),
        admin_token="admin-workspace-test",
    )
    client = TestClient(app)
    return client, app


def create_workspace(client, name):
    response = client.post("/v1/admin/workspaces", headers=ADMIN, json={"name": name})
    assert response.status_code == 201, response.text
    value = response.json()
    return value, {"Authorization": "Bearer " + value["access_token"]}


def create_installation(client, name, workspace_id):
    response = client.post(
        "/v1/admin/installations",
        headers=ADMIN,
        json={"name": name, "workspace_id": workspace_id},
    )
    assert response.status_code == 201, response.text
    value = response.json()
    return value, {"Authorization": "Bearer " + value["access_token"]}


def job_payload(installation_id, **extra):
    body = {
        "installation_id": installation_id,
        "command": "create_cube",
        "args": {"name": "Workspace Cube"},
        "authorization": {"approved": True, "scopes": ["scene.write"]},
    }
    body.update(extra)
    return body


def test_workspace_credential_enqueues_only_inside_its_workspace(tmp_path):
    client, _ = setup_client(tmp_path)
    first, first_token = create_workspace(client, "Workspace A")
    second, _ = create_workspace(client, "Workspace B")
    first_worker, _ = create_installation(client, "A Blender", first["workspace_id"])
    second_worker, _ = create_installation(client, "B Blender", second["workspace_id"])

    accepted = client.post(
        "/v1/workspaces/jobs",
        headers=first_token,
        json=job_payload(first_worker["installation_id"]),
    )
    assert accepted.status_code == 201, accepted.text

    cross_tenant = client.post(
        "/v1/workspaces/jobs",
        headers=first_token,
        json=job_payload(second_worker["installation_id"]),
    )
    assert cross_tenant.status_code == 404


def test_workspace_id_cannot_be_forged_in_request_body(tmp_path):
    client, _ = setup_client(tmp_path)
    workspace, token = create_workspace(client, "Workspace A")
    worker, _ = create_installation(client, "A Blender", workspace["workspace_id"])

    response = client.post(
        "/v1/workspaces/jobs",
        headers=token,
        json=job_payload(worker["installation_id"], workspace_id="wsp_someone_else"),
    )
    assert response.status_code == 422


def test_workspace_and_installation_credentials_are_not_interchangeable(tmp_path):
    client, _ = setup_client(tmp_path)
    workspace, workspace_token = create_workspace(client, "Workspace A")
    worker, worker_token = create_installation(client, "A Blender", workspace["workspace_id"])

    assert client.get("/v1/jobs/next", headers=workspace_token).status_code == 401
    assert client.post(
        "/v1/workspaces/jobs",
        headers=worker_token,
        json=job_payload(worker["installation_id"]),
    ).status_code == 401


def test_admin_cannot_reassign_job_across_workspace_boundary(tmp_path):
    client, _ = setup_client(tmp_path)
    first, _ = create_workspace(client, "Workspace A")
    second, _ = create_workspace(client, "Workspace B")
    first_worker, _ = create_installation(client, "A Blender", first["workspace_id"])
    second_worker, _ = create_installation(client, "B Blender", second["workspace_id"])
    created = client.post(
        "/v1/workspaces/jobs",
        headers={"Authorization": "Bearer " + first["access_token"]},
        json=job_payload(first_worker["installation_id"]),
    )
    assert created.status_code == 201

    reassigned = client.post(
        f"/v1/admin/jobs/{created.json()['id']}/assign",
        headers=ADMIN,
        json={"installation_id": second_worker["installation_id"]},
    )
    assert reassigned.status_code == 409


def test_workspace_credential_revocation_and_workspace_deactivation(tmp_path):
    client, app = setup_client(tmp_path)
    workspace, token = create_workspace(client, "Workspace A")
    worker, _ = create_installation(client, "A Blender", workspace["workspace_id"])
    credential_id = None
    with app.state.session_factory() as session:
        credential = session.query(WorkspaceCredential).filter_by(
            workspace_id=workspace["workspace_id"]
        ).one()
        credential_id = credential.id
        assert credential.token_hash == _token_hash(workspace["access_token"])

    revoked = client.post(
        f"/v1/admin/workspace-credentials/{credential_id}/revoke",
        headers=ADMIN,
    )
    assert revoked.status_code == 200
    assert client.post(
        "/v1/workspaces/jobs",
        headers=token,
        json=job_payload(worker["installation_id"]),
    ).status_code == 401

    _, active_token = create_workspace(client, "Workspace C")
    workspace_c_id = client.post(
        "/v1/admin/workspaces", headers=ADMIN, json={"name": "Workspace D"}
    ).json()["workspace_id"]
    deactivated = client.post(
        f"/v1/admin/workspaces/{workspace_c_id}/deactivate",
        headers=ADMIN,
    )
    assert deactivated.status_code == 200


def test_workspace_audit_events_are_scoped(tmp_path):
    client, _ = setup_client(tmp_path)
    first, first_token = create_workspace(client, "Workspace A")
    second, second_token = create_workspace(client, "Workspace B")
    first_worker, _ = create_installation(client, "A Blender", first["workspace_id"])
    client.post(
        "/v1/workspaces/jobs",
        headers=first_token,
        json=job_payload(first_worker["installation_id"]),
    )

    first_events = client.get("/v1/workspaces/audit-events", headers=first_token)
    second_events = client.get("/v1/workspaces/audit-events", headers=second_token)
    assert first_events.status_code == 200
    assert second_events.status_code == 200
    assert first_events.json()["events"]
    assert all(event["target_id"] != first["workspace_id"] for event in second_events.json()["events"])
