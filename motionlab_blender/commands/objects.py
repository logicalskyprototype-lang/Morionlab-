"""Small allowlist of scene operations. Called only on Blender's main thread."""

import hashlib
import json

import bpy


_JOB_MARKER = "_motionlab_job_id"
_PAYLOAD_MARKER = "_motionlab_payload_sha256"


def _payload_fingerprint(args):
    canonical = json.dumps(
        {"command": "create_cube", "args": args},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _cube_result(obj):
    return {
        "object_name": obj.name,
        "object_type": obj.type,
        "location": [float(value) for value in obj.location],
        "dimensions": [float(value) for value in obj.dimensions],
    }


def create_cube(args, job_id):
    """Create one cube per job ID, including across saved .blend reloads."""
    fingerprint = _payload_fingerprint(args)

    for existing in bpy.data.objects:
        if existing.get(_JOB_MARKER) == job_id:
            if existing.get(_PAYLOAD_MARKER) != fingerprint:
                raise ValueError(
                    "Job ID was already used with a different command payload."
                )
            if existing.name not in bpy.context.scene.objects:
                bpy.context.scene.collection.objects.link(existing)
            return _cube_result(existing)

    name = args["name"]
    size = args["size"]
    location = args["location"]

    bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    obj = bpy.context.object
    obj.name = name
    # Store the idempotency record on the scene object so it survives .blend saves.
    obj[_JOB_MARKER] = job_id
    obj[_PAYLOAD_MARKER] = fingerprint
    return _cube_result(obj)


def execute_command(job):
    if job["command"] == "create_cube":
        return create_cube(job["args"], job["id"])
    raise ValueError("Unsupported command.")
