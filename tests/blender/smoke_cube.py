"""Run with blender --background --python-exit-code 1 --python tests/blender/smoke_cube.py."""

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bpy
import motionlab_blender
from motionlab_blender import addon as addon_runtime

from motionlab_blender.protocol import validate_job
from motionlab_blender.permissions import authorize_job
from motionlab_blender.commands.objects import execute_command

bpy.ops.wm.read_factory_settings(use_empty=True)

# Verify the add-on package can register and unregister inside Blender itself.
motionlab_blender.register()
assert all(
    bpy.utils.is_registered_class(cls)
    for cls in addon_runtime._CLASSES
), "MotionLab add-on classes were not registered"
motionlab_blender.unregister()
assert not any(
    bpy.utils.is_registered_class(cls)
    for cls in addon_runtime._CLASSES
), "MotionLab add-on classes were not unregistered"

job = validate_job({
    "id": "ci-cube-001",
    "command": "create_cube",
    "args": {"name": "MotionLab CI Cube", "size": 2, "location": [1, 2, 3]},
    "authorization": {"approved": True, "scopes": ["scene.write"]},
})
authorize_job(job)
result = execute_command(job)

assert result["object_name"] == "MotionLab CI Cube"
assert result["object_type"] == "MESH"
assert len(bpy.context.scene.objects) == 1
assert tuple(round(value, 3) for value in result["location"]) == (1.0, 2.0, 3.0)
assert all(abs(value - 2.0) < 0.001 for value in result["dimensions"])

print("MOTIONLAB_ADDON_REGISTRATION=PASS")
print("MOTIONLAB_BLENDER_SMOKE_TEST=PASS")
