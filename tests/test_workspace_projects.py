from fastapi.testclient import TestClient

from backend.app import Project, WorkspaceCredential, create_app

ADMIN = {"Authorization": "Bearer project-admin-test"}


def setup(tmp_path):
    app = create_app(
        database_url="sqlite:///" + str(tmp_path / "projects.db"),
        admin_token="project-admin-test",
    )
    client = TestClient(app)
    workspace = client.post(
        "/v1/admin/workspaces", headers=ADMIN, json={"name": "Studio A"}
    ).json()
    token = {"Authorization": "Bearer " + workspace["access_token"]}
    return client, app, workspace, token


def create_project(client, token, **overrides):
    payload = {"name": "Product concept", "project_type": "3d_scene"}
    payload.update(overrides)
    return client.post("/v1/workspaces/projects", headers=token, json=payload)


def test_workspace_project_create_list_get_update_and_archive(tmp_path):
    client, app, workspace, token = setup(tmp_path)
    created = create_project(
        client, token, description="First prototype", project_type="experiment"
    )
    assert created.status_code == 201, created.text
    project = created.json()["project"]
    assert project["id"].startswith("prj_")
    assert project["name"] == "Product concept"
    assert project["description"] == "First prototype"
    assert project["project_type"] == "experiment"
    assert project["status"] == "active"

    listed = client.get("/v1/workspaces/projects", headers=token)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["projects"]] == [project["id"]]

    fetched = client.get(f"/v1/workspaces/projects/{project['id']}", headers=token)
    assert fetched.status_code == 200
    assert fetched.json()["project"]["name"] == "Product concept"

    updated = client.patch(
        f"/v1/workspaces/projects/{project['id']}",
        headers=token,
        json={"name": "Product concept v2", "description": "Updated brief"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["project"]["name"] == "Product concept v2"
    assert updated.json()["project"]["updated_at"] >= project["updated_at"]

    archived = client.patch(
        f"/v1/workspaces/projects/{project['id']}",
        headers=token,
        json={"status": "archived"},
    )
    assert archived.status_code == 200
    assert archived.json()["project"]["status"] == "archived"
    assert client.get("/v1/workspaces/projects", headers=token).json()["projects"] == []
    assert len(client.get(
        "/v1/workspaces/projects?include_archived=true", headers=token
    ).json()["projects"]) == 1

    blocked_edit = client.patch(
        f"/v1/workspaces/projects/{project['id']}",
        headers=token,
        json={"name": "Should not edit"},
    )
    assert blocked_edit.status_code == 409

    reactivated = client.patch(
        f"/v1/workspaces/projects/{project['id']}",
        headers=token,
        json={"status": "active"},
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["project"]["status"] == "active"

    with app.state.session_factory() as session:
        stored = session.get(Project, project["id"])
        assert stored is not None
        assert stored.workspace_id == workspace["workspace_id"]


def test_project_isolation_between_workspaces_and_credential_types(tmp_path):
    client, _, workspace_a, token_a = setup(tmp_path)
    workspace_b = client.post(
        "/v1/admin/workspaces", headers=ADMIN, json={"name": "Studio B"}
    ).json()
    token_b = {"Authorization": "Bearer " + workspace_b["access_token"]}
    created = create_project(client, token_a)
    project_id = created.json()["project"]["id"]

    assert client.get(
        f"/v1/workspaces/projects/{project_id}", headers=token_b
    ).status_code == 404
    assert client.patch(
        f"/v1/workspaces/projects/{project_id}",
        headers=token_b,
        json={"name": "Cross-tenant edit"},
    ).status_code == 404
    assert client.get(
        "/v1/workspaces/projects", headers=token_b
    ).json()["projects"] == []

    assert client.get("/v1/workspaces/projects").status_code == 401
    assert client.get(
        "/v1/workspaces/projects",
        headers={"Authorization": "Bearer " + "wrong-token"},
    ).status_code == 401


def test_project_validation_and_pagination_bounds(tmp_path):
    client, _, _, token = setup(tmp_path)
    assert create_project(client, token, name="   ").status_code == 422
    assert create_project(client, token, name="x" * 101).status_code == 422
    assert create_project(client, token, project_type="arbitrary").status_code == 422
    assert create_project(client, token, workspace_id="wsp_forged").status_code == 422
    assert create_project(client, token, description="x" * 2001).status_code == 422

    project = create_project(client, token).json()["project"]
    assert client.patch(
        f"/v1/workspaces/projects/{project['id']}",
        headers=token,
        json={},
    ).status_code == 422
    assert client.patch(
        f"/v1/workspaces/projects/{project['id']}",
        headers=token,
        json={"status": "deleted"},
    ).status_code == 422
    assert client.patch(
        f"/v1/workspaces/projects/{project['id']}",
        headers=token,
        json={"status": None},
    ).status_code == 422
    assert client.get(
        "/v1/workspaces/projects?limit=101", headers=token
    ).status_code == 422


def test_project_mutations_write_workspace_scoped_audit_events(tmp_path):
    client, _, _, token = setup(tmp_path)
    created = create_project(client, token).json()["project"]
    client.patch(
        f"/v1/workspaces/projects/{created['id']}",
        headers=token,
        json={"name": "Renamed"},
    )
    events = client.get("/v1/workspaces/audit-events", headers=token).json()["events"]
    project_events = [event for event in events if event["target_id"] == created["id"]]
    assert [event["event_type"] for event in reversed(project_events)] == [
        "project.created",
        "project.updated",
    ]


def test_project_api_rejects_worker_credentials_and_revoked_workspace(tmp_path):
    client, _, workspace, token = setup(tmp_path)
    worker = client.post(
        "/v1/admin/installations",
        headers=ADMIN,
        json={"name": "Blender worker", "workspace_id": workspace["workspace_id"]},
    )
    assert worker.status_code == 201, worker.text
    worker_token = {
        "Authorization": "Bearer " + worker.json()["access_token"]
    }
    assert client.get(
        "/v1/workspaces/projects", headers=worker_token
    ).status_code == 401

    # A valid workspace credential can create a project before revocation.
    created = create_project(client, token)
    assert created.status_code == 201
    credential_id = None
    # Revoke through the admin endpoint; subsequent project reads must fail.
    # The credential id is obtained from the database, never exposed to clients.
    # The test client exposes the app state for verifying the persisted credential.
    app = client.app
    with app.state.session_factory() as session:
        credential = session.query(WorkspaceCredential).filter_by(
            workspace_id=workspace["workspace_id"]
        ).one()
        credential_id = credential.id
    revoked = client.post(
        f"/v1/admin/workspace-credentials/{credential_id}/revoke",
        headers=ADMIN,
    )
    assert revoked.status_code == 200
    assert client.get("/v1/workspaces/projects", headers=token).status_code == 401
