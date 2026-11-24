"""Allowlisted scene operations with a persistent project-wide idempotency ledger."""

import hashlib
import json

import bpy


_JOB_MARKER = "_motionlab_job_id"
_PAYLOAD_MARKER = "_motionlab_payload_sha256"
_LEDGER_TEXT_NAME = "MotionLabJobLedger"


def _payload_fingerprint(args):
    canonical = json.dumps(
        {"command": "create_cube", "args": args},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_ledger():
    ledger_text = bpy.data.texts.get(_LEDGER_TEXT_NAME)
    raw = "{}" if ledger_text is None else ledger_text.as_string()
    try:
        ledger = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("MotionLab project idempotency ledger is corrupted.") from exc
    if not isinstance(ledger, dict):
        raise RuntimeError("MotionLab project idempotency ledger is invalid.")
    return ledger


def _save_ledger(ledger):
    serialized = json.dumps(ledger, sort_keys=True, separators=(",", ":"))
    ledger_text = bpy.data.texts.get(_LEDGER_TEXT_NAME)
    if ledger_text is None:
        ledger_text = bpy.data.texts.new(_LEDGER_TEXT_NAME)
    else:
        ledger_text.clear()
    ledger_text.write(serialized)


def _cube_result(obj):
    return {
        "object_name": obj.name,
        "object_type": obj.type,
        "location": [float(value) for value in obj.location],
        "dimensions": [float(value) for value in obj.dimensions],
    }


def create_cube(args, job_id):
    """Create one cube per job ID and persist its result in the .blend project."""
    scene = bpy.context.scene
    fingerprint = _payload_fingerprint(args)
    ledger = _load_ledger()
    prior = ledger.get(job_id)

    if prior is not None:
        if not isinstance(prior, dict) or "payload_sha256" not in prior or "result" not in prior:
            raise RuntimeError("MotionLab project idempotency record is invalid.")
        if prior.get("payload_sha256") != fingerprint:
            raise ValueError(
                "Job ID was already used with a different command payload."
            )
        # Return the recorded outcome even if a user later deletes the object.
        return prior["result"]

    # Recover the ledger if an object marker exists but the project record is absent.
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
            _save_ledger(ledger)
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
    _save_ledger(ledger)
    return result


def execute_command(job):
    if job["command"] == "create_cube":
        return create_cube(job["args"], job["id"])
    raise ValueError("Unsupported command.")
