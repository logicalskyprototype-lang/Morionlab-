# ThinkPad validation checklist

This checklist is intentionally pending until the laptop is available.

## Record the environment

Run these commands locally and save the output with the test notes:

```bash
blender --version
```

Inside Blender's Python Console:

```python
import bpy, sys
print("Blender:", bpy.app.version_string)
print("Bundled Python:", sys.version)
print("Executable:", sys.executable)
```

Also record the operating system, graphics driver and whether Blender starts in background mode.

## Local validation

1. Install the reviewed MotionLab Bridge add-on from the approved branch or release artifact.
2. Enable the add-on in Blender Preferences.
3. Confirm the add-on loads without registration errors.
4. Do not enter a production token until the service and credential handling have been reviewed.
5. Once a compatible service is deployed and explicitly approved, test outbound HTTPS connectivity.
6. Submit an approved cube job and verify the object in the scene.
7. Disconnect Blender and confirm the worker shuts down cleanly.
8. Record CPU, memory and viewport responsiveness on the Intel HD Graphics 520.

The GitHub Actions Blender smoke test does not replace these hardware and network checks.
