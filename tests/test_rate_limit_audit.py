from fastapi.testclient import TestClient

from backend.app import AuditEvent, RateLimitBucket, create_app

ADMIN = {"Authorization": "Bearer admin-test-token"}


def make_client(tmp_path, *, limit=20, window=60):
    database = "sqlite:///" + str(tmp_path / "rate-audit.db")
    app = create_app(
        database_url=database,
        admin_token="admin-test-token",
        rate_limit_requests=limit,
        rate_limit_window_seconds=window,
    )
    return TestClient(app), app


def test_api_rate_limit_returns_429_and_retry_after(tmp_path):
    client, _ = make_client(tmp_path, limit=2)
    assert client.get("/v1/admin/audit-events", headers=ADMIN).status_code == 200
    assert client.get("/v1/admin/audit-events", headers=ADMIN).status_code == 200

    limited = client.get("/v1/admin/audit-events", headers=ADMIN)
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1
    assert limited.json()["detail"] == "Rate limit exceeded."


def test_rate_limit_buckets_store_only_hashes_not_credentials(tmp_path):
    client, app = make_client(tmp_path, limit=10)
    secret = "Bearer highly-sensitive-test-admin-token"
    response = client.get("/v1/admin/audit-events", headers={"Authorization": secret})
    assert response.status_code == 401
    with app.state.session_factory() as session:
        buckets = session.query(RateLimitBucket).all()
        assert len(buckets) == 1
        assert len(buckets[0].bucket_key) == 64
        assert "highly-sensitive" not in buckets[0].bucket_key


def test_sensitive_operations_write_durable_audit_events(tmp_path):
    client, app = make_client(tmp_path)
    created = client.post(
        "/v1/admin/installations",
        headers=ADMIN,
        json={"name": "Audit test worker"},
    )
    assert created.status_code == 201
    installation_id = created.json()["installation_id"]

    revoked = client.post(
        f"/v1/admin/installations/{installation_id}/revoke",
        headers=ADMIN,
    )
    assert revoked.status_code == 200

    listed = client.get("/v1/admin/audit-events", headers=ADMIN)
    assert listed.status_code == 200
    types = [event["event_type"] for event in listed.json()["events"]]
    assert "installation.created" in types
    assert "installation.revoked" in types

    with app.state.session_factory() as session:
        events = session.query(AuditEvent).all()
        assert len(events) >= 2
        assert all("access_token" not in event.details_json for event in events)


def test_audit_event_and_domain_change_commit_together(tmp_path):
    client, app = make_client(tmp_path)
    created = client.post(
        "/v1/admin/installations",
        headers=ADMIN,
        json={"name": "Atomic audit worker"},
    )
    assert created.status_code == 201
    installation_id = created.json()["installation_id"]
    with app.state.session_factory() as session:
        event = session.query(AuditEvent).filter_by(
            event_type="installation.created",
            target_id=installation_id,
        ).one()
        assert event.actor_type == "admin"
        assert event.target_type == "installation"
