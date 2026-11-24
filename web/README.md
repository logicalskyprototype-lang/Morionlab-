# MotionLab web application

This directory contains the first browser-based MotionLab application shell.

## Run locally

From the repository root:

```bash
python -m http.server 4173
```

Open `http://localhost:4173/web/`.

No JavaScript package manager or third-party runtime dependency is required for this first shell. The UI uses native browser JavaScript and CSS. This keeps the initial interface easy to inspect and run from a lightweight development environment. A framework/build pipeline can be introduced when the application needs richer state management, component reuse, and production asset bundling.

## Current scope

- Responsive dashboard and navigation.
- Project list, search, and a non-persistent project setup preview.
- Creative workspace layout preview with scene hierarchy, viewport, properties, and timeline.
- Placeholder sections for assets, experiments, animation, AI studio, connections, and settings.

## Deliberate limitations

This is an interface foundation, not a connected customer application yet. Project cards are sample data. Project creation does not save. There is no browser-held workspace credential, login flow, live job API, real scene editing, asset upload, render submission, or cloud persistence.

Workspace credentials must remain server-side. The browser must authenticate a human through the trusted Grey/orchestrator integration; it must never receive the MotionLab workspace service credential or global admin token.

The next increments should add a proper human-authenticated backend-for-frontend session, workspace/project APIs, and live Blender connection status before enabling project creation or execution actions.
