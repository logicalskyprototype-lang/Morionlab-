# MotionLab backend

This service implements the v0.2 installation-scoped job API consumed by the Blender bridge.

## Local development

Requires Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
export MOTIONLAB_ADMIN_TOKEN='replace-with-a-long-random-admin-token'
uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

The default database is SQLite at `./motionlab.db`. Set `DATABASE_URL` to a PostgreSQL SQLAlchemy URL for PostgreSQL testing, for example `postgresql+psycopg://user:password@host:5432/motionlab`.

Run tests with `python -m pytest -q`. Apply schema migrations with `alembic upgrade head`.

## API

- `GET /healthz`: liveness check.
- `POST /v1/admin/installations`: provision an installation. Returns its bearer token once; only the SHA-256 token hash is stored.
- `POST /v1/admin/installations/{installation_id}/revoke`: revoke credentials and release that installation's unfinished jobs.
- `POST /v1/admin/jobs`: enqueue a job explicitly assigned to an active installation.
- `POST /v1/admin/jobs/{job_id}/assign`: assign or reassign a queued job to an active installation.
- `GET /v1/jobs/next`: lease a job only to the authenticated installation. The response includes a one-time lease token.
- `POST /v1/jobs/{job_id}/result`: submit the result with the current lease token.

All `/v1/admin/*` routes require the server-side admin bearer token. Blender installations receive distinct credentials. Lease tokens are rotated on redelivery, and only the current unexpired token can acknowledge a job.

Only `create_cube` is currently supported. The server requires `approved: true` and the `scene.write` scope, then validates the command arguments again. A trusted server-side orchestrator must verify the user's explicit approval before calling the admin endpoint. Never put the admin token in Grey's browser UI, Blender, mobile clients, or any public client.

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
