# MotionLab backend

This service implements the v0.2 installation-scoped contract consumed by `motionlab_blender.connector`.

## Local development

Requires Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
export MOTIONLAB_ADMIN_TOKEN='replace-with-a-long-random-server-only-token'
uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

The default database is SQLite at `./motionlab.db`. Set `DATABASE_URL` to a PostgreSQL SQLAlchemy URL for production-like testing, for example `postgresql+psycopg://user:password@host:5432/motionlab`.

Run tests with `python -m pytest -q`.

## API

- `GET /healthz`: liveness check.
- `POST /v1/admin/installations`: provision an installation. Returns its access token once.
- `POST /v1/admin/installations/{installation_id}/revoke`: revoke credentials and release its unfinished assigned jobs.
- `POST /v1/admin/jobs`: enqueue a job for a specific active installation.
- `POST /v1/admin/jobs/{job_id}/assign`: reassign a queued job to another active installation.
- `GET /v1/jobs/next`: lease the next job assigned to the authenticated installation.
- `POST /v1/jobs/{job_id}/result`: accept the saved Blender result using its current lease token.

The service issues a random installation credential and stores only its SHA-256 hash. The plaintext credential is returned only at provisioning time. Revocation is immediate for subsequent requests. Each installation has its own lease row, so simultaneous pollers for one installation cannot obtain overlapping jobs while different installations remain isolated.

Every lease receives a new random fencing token. Only the token hash is stored. A result from a previous lease attempt is rejected after a job is redelivered. The Blender add-on carries the token through its save-gated result path.

Only `create_cube` is supported. The server requires `approved: true` and the `scene.write` scope, and validates command arguments again.

## Trust and deployment boundaries

The admin bearer token is a privileged server-side credential. It can provision/revoke installations, assign jobs, and enqueue work. Never put it in Grey's browser UI, Blender, mobile clients, or any public client. A trusted server-side orchestrator must verify user identity, workspace ownership, and explicit user approval before calling administrative endpoints. The `approved` field is not proof by itself; it is a claim made by the trusted admin caller.

Installation credentials authenticate a worker installation, not a human user or tenant. This increment does not yet implement user/workspace authorization, credential rotation, rate limiting, audit logging, database migrations for an existing deployment, or production secret management. Treat the schema as a development/test schema until an explicit migration plan is reviewed.

This is not a deployed service. Do not expose it publicly until HTTPS termination, persistent PostgreSQL, migrations, admin credential management, rate limiting, operational logging, backups, and production deployment configuration have been reviewed. SQLite is for deterministic development tests, not concurrent production workers.
