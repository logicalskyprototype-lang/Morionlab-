# MotionLab

MotionLab is LogicalSky's Creative Engineering Lab for interactive experiments, 3D scenes, simulations, animation tools and future AI-assisted creative workflows.

## Current increment: v0.3 workspace backend + web application foundation

The first increment establishes a constrained, outbound-only bridge for approved Blender scene jobs. It currently implements one allowlisted operation: create a cube.

The repository includes a v0.3 FastAPI service with workspace-scoped credentials, tenant isolation, installation-scoped workers, PostgreSQL lease locking, rate limiting, durable audit events and fenced result acknowledgements. The workspace authorization implementation is merged and CI-verified. The service is not deployed. See [the backend guide](backend/README.md), [the security model](docs/security-model.md), and [the architecture](docs/architecture.md).

The responsive browser application in [`web/`](web/README.md) is now served by the FastAPI service on the same origin. This increment adds public email magic-link authentication, personal workspaces, browser sessions, and workspace-scoped project listing/creation. Email delivery is still a development outbox only; no production sender or domain is configured. Live Blender scene editing, asset uploads, and cloud execution remain future work.

## Development

Run the Python tests:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Run the Blender 3.6 headless smoke test:

```bash
blender --background --python tests/blender/smoke_cube.py
```

GitHub Actions runs both test groups. Real ThinkPad, graphics-driver and service-connection checks remain pending.
