"""Run with blender --background --python-exit-code 1 --python tests/blender/smoke_cube.py."""

import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bpy
import motionlab_blender

from motionlab_blender.protocol import validate_job
from motionlab_blender.permissions import authorize_job
from motionlab_blender.commands.objects import execute_command

bpy.ops.wm.read_factory_settings(use_empty=True)

# Registration must complete and expose the bridge operator to Blender.
motionlab_blender.register()
assert callable(bpy.ops.motionlab.connect), "MotionLab connect operator was not registered"
motionlab_blender.unregister()

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

# Redelivery in the same session must return the recorded result, not create another.
replayed = execute_command(job)
assert replayed == result
assert len(bpy.context.scene.objects) == 1

# The scene ledger must survive saving and reopening a .blend file.
with tempfile.TemporaryDirectory() as temp_dir:
    scene_path = os.path.join(temp_dir, "motionlab-idempotency.blend")
    bpy.ops.wm.save_as_mainfile(filepath=scene_path)
    bpy.ops.wm.open_mainfile(filepath=scene_path)

    replayed_after_reload = execute_command(job)
    assert replayed_after_reload == result
    assert len(bpy.context.scene.objects) == 1

    # The project-wide ledger also prevents replay from another active scene.
    original_scene = bpy.context.scene
    alternate_scene = bpy.data.scenes.new("MotionLab Alternate Scene")
    bpy.context.window.scene = alternate_scene
    replayed_from_other_scene = execute_command(job)
    assert replayed_from_other_scene == result
    assert len(alternate_scene.objects) == 0
    bpy.context.window.scene = original_scene

    # Even if the user deletes the created object, the project ledger prevents replay.
    bpy.data.objects.remove(bpy.data.objects["MotionLab CI Cube"], do_unlink=True)
    replayed_after_delete = execute_command(job)
    assert replayed_after_delete == result
    assert len(bpy.context.scene.objects) == 0

# Reusing a job ID with a different payload must be rejected.
conflicting_job = dict(job)
conflicting_job["args"] = dict(job["args"], name="Different Cube")
try:
    execute_command(conflicting_job)
except ValueError as exc:
    assert "different command payload" in str(exc)
else:
    raise AssertionError("Conflicting job ID/payload was not rejected")
assert len(bpy.context.scene.objects) == 0

print("MOTIONLAB_ADDON_REGISTRATION=PASS")
print("MOTIONLAB_CUBE_IDEMPOTENCY_SAME_SESSION=PASS")
print("MOTIONLAB_CUBE_IDEMPOTENCY_AFTER_RELOAD=PASS")
print("MOTIONLAB_CUBE_IDEMPOTENCY_CROSS_SCENE=PASS")
print("MOTIONLAB_CUBE_IDEMPOTENCY_AFTER_DELETE=PASS")
print("MOTIONLAB_CUBE_IDEMPOTENCY_CONFLICT=PASS")
print("MOTIONLAB_BLENDER_SMOKE_TEST=PASS")
