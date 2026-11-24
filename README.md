# MotionLab

MotionLab is LogicalSky's Creative Engineering Lab for interactive experiments, 3D scenes, simulations, animation tools and future AI-assisted creative workflows.

## Current increment: v0.3 workspace backend + web application foundation

The first increment establishes a constrained, outbound-only bridge for approved Blender scene jobs. It currently implements one allowlisted operation: create a cube.

The repository includes a v0.3 FastAPI service with workspace-scoped credentials, tenant isolation, installation-scoped workers, PostgreSQL lease locking, rate limiting, durable audit events and fenced result acknowledgements. The workspace authorization implementation is merged and CI-verified. The service is not deployed. See [the backend guide](backend/README.md), [the security model](docs/security-model.md), and [the architecture](docs/architecture.md).

The first browser-based MotionLab interface foundation is under development in [`web/`](web/README.md). It includes the responsive dashboard, project navigation and a creative workspace preview. Project persistence, human authentication, live Blender editing and cloud execution are not connected yet.

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
