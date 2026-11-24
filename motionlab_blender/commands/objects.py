"""Allowlisted scene operations with a persistent per-scene idempotency ledger."""

import hashlib
import json

import bpy


_JOB_MARKER = "_motionlab_job_id"
_PAYLOAD_MARKER = "_motionlab_payload_sha256"
_SCENE_LEDGER = "_motionlab_job_ledger"


def _payload_fingerprint(args):
    canonical = json.dumps(
        {"command": "create_cube", "args": args},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_ledger(scene):
    raw = scene.get(_SCENE_LEDGER, "{}")
    try:
        ledger = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("MotionLab scene idempotency ledger is corrupted.") from exc
    if not isinstance(ledger, dict):
        raise RuntimeError("MotionLab scene idempotency ledger is invalid.")
    return ledger


def _save_ledger(scene, ledger):
    scene[_SCENE_LEDGER] = json.dumps(
        ledger,
        sort_keys=True,
        separators=(",", ":"),
    )


def _cube_result(obj):
    return {
        "object_name": obj.name,
        "object_type": obj.type,
        "location": [float(value) for value in obj.location],
        "dimensions": [float(value) for value in obj.dimensions],
    }


def create_cube(args, job_id):
    """Create one cube per job ID and persist its result in the active scene."""
    scene = bpy.context.scene
    fingerprint = _payload_fingerprint(args)
    ledger = _load_ledger(scene)
    prior = ledger.get(job_id)

    if prior is not None:
        if prior.get("payload_sha256") != fingerprint:
            raise ValueError(
                "Job ID was already used with a different command payload."
            )
        # Return the recorded outcome even if a user later deletes the object.
        return prior["result"]

    # Recover the ledger if an object marker exists but the scene record is absent.
    for existing in bpy.data.objects:
        if existing.get(_JOB_MARKER) == job_id:
            if existing.get(_PAYLOAD_MARKER) != fingerprint:
                raise ValueError(
                    "Job ID was already used with a different command payload."
                )
            if existing.name not in scene.objects:
                scene.collection.objects.link(existing)
            result = _cube_result(existing)
            ledger[job_id] = {
                "payload_sha256": fingerprint,
                "result": result,
            }
            _save_ledger(scene, ledger)
            return result

    bpy.ops.mesh.primitive_cube_add(
        size=args["size"],
        location=args["location"],
    )
    obj = bpy.context.object
    obj.name = args["name"]
    obj[_JOB_MARKER] = job_id
    obj[_PAYLOAD_MARKER] = fingerprint

    result = _cube_result(obj)
    ledger[job_id] = {
        "payload_sha256": fingerprint,
        "result": result,
    }
    _save_ledger(scene, ledger)
    return result


def execute_command(job):
    if job["command"] == "create_cube":
        return create_cube(job["args"], job["id"])
    raise ValueError("Unsupported command.")
