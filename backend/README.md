# MotionLab backend

This service implements the v0.1 contract consumed by `motionlab_blender.connector`.

## Local development

Requires Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
export MOTIONLAB_ADDON_TOKEN='replace-with-a-long-random-addon-token'
export MOTIONLAB_ADMIN_TOKEN='replace-with-a-separate-long-random-admin-token'
uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

The default database is SQLite at `./motionlab.db`. Set `DATABASE_URL` to a PostgreSQL SQLAlchemy URL for production-like testing, for example `postgresql+psycopg://user:password@host:5432/motionlab`.

Run tests with `python -m pytest -q`.

## API

- `GET /healthz`: liveness check.
- `POST /v1/admin/jobs`: enqueue a job. Requires the admin bearer token.
- `GET /v1/jobs/next`: lease the next job. Requires the add-on bearer token.
- `POST /v1/jobs/{job_id}/result`: accept the saved Blender result. Requires the add-on bearer token.

The initial service deliberately supports one configured add-on installation/token. It allows only `create_cube`, requires `approved: true` and the `scene.write` scope, and validates command arguments again on the server.

**Trust boundary:** the admin bearer token is a privileged server-side credential. Possession of it authorizes job creation; the `approved` field records the approval decision made by that trusted caller. Never put this token in the Grey browser UI, Blender add-on, mobile clients, or any public client. A trusted server-side orchestrator must verify the user's explicit approval before calling the admin endpoint. The add-on token is a separate, lower-privilege credential and cannot enqueue jobs.

## Deployment boundary

This is a backend implementation, not a deployed service. Do not expose it publicly until production authentication, HTTPS termination, persistent PostgreSQL, installation-specific credentials, lease concurrency, rate limiting, structured operational logging, backups and deployment configuration have been reviewed.

The current add-on credential represents one installation. Before multi-device or multi-user use, add an installation registry and bind each lease to a distinct installation identity. SQLite is for deterministic development tests, not concurrent production workers.
