# MotionLab web application

The browser application is served by the FastAPI service on the same origin as its authentication and project APIs.

## Run locally

```bash
python -m pip install -r requirements-dev.txt
export MOTIONLAB_ADMIN_TOKEN='replace-with-a-long-random-admin-token'
uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/`. The FastAPI service serves `web/index.html`, `/web/app.js`, and `/web/styles.css`, so the browser session cookie and API requests remain same-origin.

In development, magic-link requests are captured in the in-memory `app.state.magic_link_outbox` for automated tests. The outbox is not exposed through an HTTP endpoint and no email is sent. Production requests fail closed until an email sender is explicitly injected into `create_app`.

## Current scope

- Responsive MotionLab dashboard and navigation.
- Public email magic-link signup and sign-in.
- Personal workspace creation after email verification.
- HttpOnly browser sessions and sign out.
- Workspace-scoped project listing and project creation.
- Creative workspace layout preview with scene hierarchy, viewport, properties, and timeline.
- Placeholder sections for assets, experiments, animation, AI studio, connections, and settings.

## Deliberate limitations

Authentication is not production-ready until a real transactional email sender and verified sending domain are configured, production HTTPS and trusted-proxy behavior are reviewed, and the new database migration is applied to a backed-up staging database. OTPless, email OTP, Grey identity handoff, Blender scene editing, asset upload, rendering, and RunPod execution are not included in this increment.

Workspace credentials remain server-side. The browser uses only its own revocable session cookie and never receives a workspace service credential or global admin token.
