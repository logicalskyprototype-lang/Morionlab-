# MotionLab backend

This service implements the v0.4 workspace-scoped project and job APIs and installation-scoped worker contract consumed by the Blender bridge.

## Local development

Requires Python 3.11+.

For a production runtime install, use `requirements.txt`. For local development and tests, use `requirements-dev.txt`.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
export MOTIONLAB_ADMIN_TOKEN='replace-with-a-long-random-admin-token'
uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

The default database is SQLite at `./motionlab.db`. Set `DATABASE_URL` to a PostgreSQL SQLAlchemy URL for PostgreSQL testing, for example `postgresql+psycopg://user:password@host:5432/motionlab`. For an existing database, run `alembic upgrade head` before starting the updated service; `create_all()` does not alter old tables.

Run tests with `python -m pytest -q`. Apply schema migrations with `alembic upgrade head`.

## API

- `GET /healthz`: liveness check.
- `GET /readyz`: database connectivity readiness check.
- `GET /v1/admin/audit-events`: read the latest durable audit events (admin only, maximum 500 per request).
- `POST /v1/admin/workspaces`: create a workspace and issue its first workspace credential once.
- `POST /v1/admin/workspaces/{workspace_id}/credentials`: issue an additional workspace credential.
- `POST /v1/admin/workspace-credentials/{credential_id}/revoke`: revoke a workspace credential.
- `POST /v1/admin/workspaces/{workspace_id}/deactivate`: deactivate a workspace.
- `POST /v1/admin/installations`: provision an installation for a selected workspace (defaults to the legacy workspace). Returns its bearer token once.
- `POST /v1/admin/installations/{installation_id}/revoke`: revoke credentials and release that installation's unfinished jobs.
- `POST /v1/admin/jobs`: admin-only job enqueue for an active installation.
- `POST /v1/admin/jobs/{job_id}/assign`: assign or reassign a queued job, only within the same workspace.
- `POST /v1/workspaces/installations`, `GET /v1/workspaces/installations`, and `POST /v1/workspaces/installations/{installation_id}/revoke`: manage Blender installations within the authenticated workspace.
- `POST /v1/workspaces/jobs`: enqueue a job for an installation in the authenticated workspace.
- `POST /v1/workspaces/projects`: create a persistent project in the authenticated workspace.
- `GET /v1/workspaces/projects`: list active projects (`include_archived=true` includes archived records; `limit` defaults to 50 and is capped at 100).
- `GET /v1/workspaces/projects/{project_id}`: retrieve a project owned by the authenticated workspace.
- `PATCH /v1/workspaces/projects/{project_id}`: update a project name/description or archive/reactivate it. Archived projects must be reactivated before editing.
- `GET /v1/workspaces/audit-events`: read audit history for the authenticated workspace only.
- `GET /v1/jobs/next`: lease a job only to the authenticated installation. The response includes a one-time lease token.
- `POST /v1/jobs/{job_id}/result`: submit the result with the current lease token.

Admin credentials, workspace credentials, and installation credentials are separate principals. Workspace identity is derived from the workspace bearer credential; request bodies cannot select a workspace. Workspace credentials are server-to-server secrets and must never be placed in browser JavaScript, mobile clients, or Blender. The trusted Grey/orchestrator service must authenticate the human user, check workspace membership and permissions, and verify explicit approval before making workspace API calls. Blender installations receive distinct credentials. Lease tokens are rotated on redelivery, and only the current unexpired token can acknowledge a job.

## Project persistence

Projects are stored in `motionlab_projects` and are always scoped to the authenticated workspace. Project IDs are server-generated. The API never accepts a caller-selected workspace ID. Names are limited to 100 characters, descriptions to 2,000 characters, and project types to `3d_scene`, `experiment`, or `animation`. Active projects are listed by default; archived projects remain recoverable with `include_archived=true`. Project create/update/archive operations emit workspace-scoped audit events in the same transaction.

The project API stores project metadata only. It does not yet store scene files, assets, Blender state, render outputs, or animation data.

## API abuse controls and audit events

All `/v1/*` requests use a persistent fixed-window rate limit. The default is 120 requests per 60 seconds per valid admin or installation credential. Requests with missing or invalid credentials share a client-IP bucket. `request.client.host` is used; forwarded headers are deliberately ignored unless a future deployment explicitly configures trusted proxies. When the limit is exceeded, the API returns HTTP 429 with `Retry-After`. If the rate-limit store is unavailable, protected API traffic fails closed with HTTP 503.

The server stores only a SHA-256 digest of each rate-limit identity, not bearer credentials. Expired buckets are cleaned opportunistically. Configure `rate_limit_requests` and `rate_limit_window_seconds` through `create_app` when constructing the service.

Installation creation/revocation, job creation/assignment/lease, and accepted job results write audit events in the same database transaction as the corresponding state change. Audit details are intentionally allowlisted and must never contain access tokens, lease tokens, or authorization headers. Administrators can inspect recent entries through `GET /v1/admin/audit-events?limit=100`. The audit endpoint is read-only; audit events are not editable through the API.

Only `create_cube` is currently supported. The server requires `approved: true` and the `scene.write` scope, then validates the command arguments again. A trusted server-side orchestrator must verify the user's identity, workspace membership, permissions, and explicit approval before calling the workspace endpoint. Never put admin or workspace credentials in Grey's browser UI, Blender, mobile clients, or any public client.

## Production startup

Set `MOTIONLAB_ENV=production`, a PostgreSQL `DATABASE_URL`, and a
`MOTIONLAB_ADMIN_TOKEN` containing at least 32 characters. Production startup
refuses SQLite and does not run `create_all()`. Apply schema changes explicitly
before starting the service:

```bash
alembic upgrade head
```

The `/healthz` endpoint is a process liveness check. Use `/readyz` for a
database connectivity readiness check. A successful readiness response does
not replace migration verification.

## Deployment boundary

This is a backend implementation, not a deployed service. Do not expose it publicly until database migrations for existing deployments, production secrets management, HTTPS termination, persistent PostgreSQL, rate limiting, structured operational logging, backups, and deployment configuration have been reviewed. SQLite is for deterministic development tests, not concurrent production workers.
