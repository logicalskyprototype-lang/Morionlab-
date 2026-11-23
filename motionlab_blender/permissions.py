"""Local authorization checks for commands delivered by MotionLab."""

from typing import Any, Dict

from .protocol import ProtocolError


def authorize_job(job: Dict[str, Any]) -> None:
    """Require explicit approval and the scene-write scope for every job."""
    authorization = job.get("authorization")
    if not isinstance(authorization, dict):
        raise ProtocolError("Authorization metadata is required.")
    if authorization.get("approved") is not True:
        raise ProtocolError("Job has not been explicitly approved.")

    scopes = authorization.get("scopes")
    if not isinstance(scopes, list) or "scene.write" not in scopes:
        raise ProtocolError("Job is missing the scene.write permission.")
