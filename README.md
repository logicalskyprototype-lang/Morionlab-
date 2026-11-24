# MotionLab

MotionLab is LogicalSky's Creative Engineering Lab for interactive experiments, 3D scenes, simulations, animation tools and future AI-assisted creative workflows.

## Current increment: v0.1 Blender Bridge

The first increment establishes a constrained, outbound-only bridge for approved Blender scene jobs. It currently implements one allowlisted operation: create a cube.

The repository now includes a v0.2 FastAPI job service with installation-scoped credentials, targeted jobs, PostgreSQL lease locking and fenced result acknowledgements. It is not deployed. See [the backend guide](backend/README.md) and [the architecture](docs/architecture.md) for the service contract, threading model and security boundaries.

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
