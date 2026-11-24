from urllib.parse import urlsplit, parse_qs

from fastapi.testclient import TestClient

from backend.app import (
    BrowserSession,
    MagicLinkChallenge,
    Project,
    UserAccount,
    Workspace,
    WorkspaceMembership,
    create_app,
)

ORIGIN = {"Origin": "http://testserver"}


def auth_client(tmp_path):
    app = create_app(
        database_url="sqlite:///" + str(tmp_path / "auth.db"),
        admin_token="admin-test-token",
    )
    return TestClient(app), app


def request_link(client, email):
    response = client.post(
        "/v1/auth/magic-link",
        headers=ORIGIN,
        json={"email": email},
    )
    assert response.status_code == 202, response.text
    return response.json()


def get_token(app, index=-1):
    link = app.state.magic_link_outbox[index]["url"]
    return parse_qs(urlsplit(link).fragment)["auth_token"][0]


def verify(client, token):
    return client.post(
        "/v1/auth/verify",
        headers=ORIGIN,
        json={"token": token},
    )


def test_public_signup_creates_verified_user_and_private_workspace(tmp_path):
    client, app = auth_client(tmp_path)
    response_body = request_link(client, "New.User@example.com")
    assert response_body == {
        "message": "If the address can receive a sign-in link, one will be sent."
    }
    token = get_token(app)
    verified = verify(client, token)
    assert verified.status_code == 200, verified.text
    assert verified.json()["authenticated"] is True
    assert verified.json()["email"] == "new.user@example.com"
    assert "motionlab_session" in verified.cookies
    cookie = verified.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie

    me = client.get("/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "new.user@example.com"
    assert me.json()["workspace_name"] == "Personal workspace"

    with app.state.session_factory() as session:
        user = session.query(UserAccount).one()
        membership = session.query(WorkspaceMembership).one()
        workspace = session.get(Workspace, membership.workspace_id)
        browser_session = session.query(BrowserSession).one()
        assert user.verified_at is not None
        assert membership.user_id == user.id
        assert membership.role == "owner"
        assert workspace.name == "Personal workspace"
        assert browser_session.token_hash != verified.cookies["motionlab_session"]
        assert len(browser_session.token_hash) == 64


def test_magic_link_is_single_use_and_expired_links_fail(tmp_path):
    client, app = auth_client(tmp_path)
    request_link(client, "person@example.com")
    token = get_token(app)
    assert verify(client, token).status_code == 200
    replay = verify(client, token)
    assert replay.status_code == 400

    request_link(client, "other@example.com")
    expired_token = get_token(app)
    with app.state.session_factory() as session:
        challenge = session.query(MagicLinkChallenge).filter_by(
            email="other@example.com"
        ).one()
        from datetime import datetime, timedelta, timezone
        challenge.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()
    assert verify(client, expired_token).status_code == 400


def test_existing_user_reuses_account_and_personal_workspace(tmp_path):
    client, app = auth_client(tmp_path)
    request_link(client, "person@example.com")
    verify(client, get_token(app))
    with app.state.session_factory() as session:
        first_workspace_id = session.query(WorkspaceMembership).one().workspace_id

    request_link(client, "PERSON@example.com")
    verify(client, get_token(app))
    with app.state.session_factory() as session:
        assert session.query(UserAccount).count() == 1
        assert session.query(WorkspaceMembership).count() == 1
        assert session.query(Workspace).filter(Workspace.id != "wsp_legacy").count() == 1
        assert session.query(WorkspaceMembership).one().workspace_id == first_workspace_id


def test_browser_projects_are_scoped_to_authenticated_workspace(tmp_path):
    client, app = auth_client(tmp_path)
    request_link(client, "first@example.com")
    verify(client, get_token(app))
    created = client.post(
        "/v1/app/projects",
        headers=ORIGIN,
        json={"name": "My scene", "project_type": "3d_scene"},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    assert client.get("/v1/app/projects").json()["projects"][0]["name"] == "My scene"

    second_client = TestClient(app)
    request_link(second_client, "second@example.com")
    verify(second_client, get_token(app))
    assert second_client.get("/v1/app/projects").json()["projects"] == []
    denied = second_client.patch(
        f"/v1/app/projects/{project_id}",
        headers=ORIGIN,
        json={"name": "Hijacked"},
    )
    assert denied.status_code == 404
    with app.state.session_factory() as session:
        stored = session.get(Project, project_id)
        assert stored.name == "My scene"


def test_logout_revokes_browser_session(tmp_path):
    client, app = auth_client(tmp_path)
    request_link(client, "person@example.com")
    verify(client, get_token(app))
    assert client.get("/v1/auth/me").status_code == 200
    logout = client.post("/v1/auth/logout", headers=ORIGIN)
    assert logout.status_code == 200
    assert client.get("/v1/auth/me").status_code == 401
    with app.state.session_factory() as session:
        browser_session = session.query(BrowserSession).one()
        assert browser_session.revoked_at is not None


def test_auth_mutations_require_same_origin(tmp_path):
    client, app = auth_client(tmp_path)
    assert client.post(
        "/v1/auth/magic-link",
        json={"email": "person@example.com"},
    ).status_code == 403
    assert client.post(
        "/v1/auth/verify",
        json={"token": "x" * 32},
    ).status_code == 403


def test_magic_link_tokens_are_stored_only_as_hashes(tmp_path):
    client, app = auth_client(tmp_path)
    request_link(client, "person@example.com")
    token = get_token(app)
    with app.state.session_factory() as session:
        challenge = session.query(MagicLinkChallenge).one()
        assert challenge.token_hash != token
        assert len(challenge.token_hash) == 64


def test_invalid_email_is_rejected(tmp_path):
    client, _ = auth_client(tmp_path)
    response = client.post(
        "/v1/auth/magic-link",
        headers=ORIGIN,
        json={"email": "not-an-email"},
    )
    assert response.status_code == 422


def test_backend_serves_browser_app_and_assets_same_origin(tmp_path):
    client, _ = auth_client(tmp_path)
    root = client.get("/")
    script = client.get("/web/app.js")
    styles = client.get("/web/styles.css")
    assert root.status_code == 200
    assert "MotionLab" in root.text
    assert script.status_code == 200
    assert "bootstrapAuth" in script.text
    assert styles.status_code == 200


def test_magic_link_token_is_only_in_url_fragment(tmp_path):
    client, app = auth_client(tmp_path)
    request_link(client, "fragment@example.com")
    link = app.state.magic_link_outbox[-1]["url"]
    parsed = urlsplit(link)
    assert parsed.query == ""
    assert parsed.fragment.startswith("auth_token=")
