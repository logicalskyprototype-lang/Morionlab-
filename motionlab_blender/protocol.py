"""Validated, deliberately small command protocol for the MotionLab bridge."""

from __future__ import annotations

import math
import re
from typing import Any, Dict


MAX_OBJECT_NAME_LENGTH = 64
MAX_COORDINATE = 100_000.0
MIN_CUBE_SIZE = 0.001
MAX_CUBE_SIZE = 1_000.0
_JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class ProtocolError(ValueError):
    """Raised when a bridge message is malformed or unsafe."""


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_job(job: Any) -> Dict[str, Any]:
    """Validate a job received from the trusted MotionLab service."""
    if not isinstance(job, dict):
        raise ProtocolError("Job must be a JSON object.")

    job_id = job.get("id")
    command = job.get("command")
    args = job.get("args", {})
    authorization = job.get("authorization")
    lease_token = job.get("lease_token")

    if not isinstance(job_id, str) or not _JOB_ID_PATTERN.fullmatch(job_id):
        raise ProtocolError("Job id must contain 1-128 letters, numbers, '_' or '-'.")
    if command != "create_cube":
        raise ProtocolError("Unsupported command.")
    if not isinstance(args, dict):
        raise ProtocolError("Command args must be an object.")
    if not isinstance(authorization, dict):
        raise ProtocolError("Authorization metadata is required.")
    if not isinstance(lease_token, str) or not lease_token or len(lease_token) > 128:
        raise ProtocolError("A valid lease token is required.")

    name = args.get("name", "MotionLab Cube")
    size = args.get("size", 2.0)
    location = args.get("location", [0.0, 0.0, 0.0])

    if not isinstance(name, str) or not name.strip() or len(name) > MAX_OBJECT_NAME_LENGTH:
        raise ProtocolError("Object name must contain 1-64 characters.")
    if not _is_number(size) or not math.isfinite(size):
        raise ProtocolError("Cube size must be a finite number.")
    if not MIN_CUBE_SIZE <= size <= MAX_CUBE_SIZE:
        raise ProtocolError("Cube size is outside the permitted range.")
    if not isinstance(location, (list, tuple)) or len(location) != 3:
        raise ProtocolError("Location must contain exactly three coordinates.")
    if any(
        not _is_number(axis)
        or not math.isfinite(axis)
        or abs(axis) > MAX_COORDINATE
        for axis in location
    ):
        raise ProtocolError("Location contains an invalid coordinate.")

    return {
        "id": job_id,
        "command": command,
        "args": {
            "name": name.strip(),
            "size": float(size),
            "location": [float(axis) for axis in location],
        },
        "authorization": authorization,
        "lease_token": lease_token,
    }
