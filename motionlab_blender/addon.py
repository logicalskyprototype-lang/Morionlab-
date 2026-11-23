"""Blender-specific registration and main-thread execution."""

import bpy

from .command_queue import CommandQueue
from .connector import ConnectorClient, NetworkWorker
from .permissions import authorize_job
from .protocol import ProtocolError
from .commands.objects import execute_command

_COMMANDS = CommandQueue(maxsize=100)
_WORKER = None
_MAX_JOBS_PER_TICK = 10


def _process_queue():
    global _WORKER
    processed = 0
    while processed < _MAX_JOBS_PER_TICK:
        job = _COMMANDS.get_nowait()
        if job is None:
            break
        result = {"job_id": job["id"], "status": "failed"}
        try:
            authorize_job(job)
            result.update({
                "status": "completed",
                "output": execute_command(job),
            })
        except Exception as exc:
            # Keep one malformed or failed command from terminating the timer.
            result["error"] = str(exc)
        finally:
            if _WORKER is not None:
                _WORKER.submit_result(job["id"], result)
            _COMMANDS.task_done()
        processed += 1

    # Yield promptly so a backlog cannot monopolize Blender's UI thread.
    return 0.01 if not _COMMANDS.empty() else 0.25


def _enqueue_job(job):
    if not _COMMANDS.put(job):
        # The server must retain/re-deliver leased jobs until their result arrives.
        from .connector import ConnectorError
        raise ConnectorError("Local command queue is full; job was not accepted.")


class MOTIONLAB_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    service_url: bpy.props.StringProperty(
        name="Service URL",
        description="HTTPS URL of the MotionLab job service",
        default="https://localhost",
    )
    access_token: bpy.props.StringProperty(
        name="Access token",
        description="Bearer token issued by the MotionLab service",
        subtype="PASSWORD",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "service_url")
        layout.prop(self, "access_token")
        layout.operator("motionlab.connect", icon="URL")


class MOTIONLAB_OT_Connect(bpy.types.Operator):
    bl_idname = "motionlab.connect"
    bl_label = "Connect to MotionLab"
    bl_description = "Start the outbound HTTPS connection"

    def execute(self, context):
        global _WORKER
        preferences = context.preferences.addons[__package__].preferences
        try:
            client = ConnectorClient(
                preferences.service_url,
                preferences.access_token,
            )
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        if _WORKER is not None and not _WORKER.stop():
            self.report({"ERROR"}, "Previous connection is still shutting down")
            return {"CANCELLED"}

        _WORKER = NetworkWorker(client, _enqueue_job)
        _WORKER.start()
        if not bpy.app.timers.is_registered(_process_queue):
            bpy.app.timers.register(_process_queue, first_interval=0.25)
        self.report({"INFO"}, "MotionLab connection worker started")
        return {"FINISHED"}


_CLASSES = (
    MOTIONLAB_Preferences,
    MOTIONLAB_OT_Connect,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    global _WORKER
    if _WORKER is not None:
        if not _WORKER.stop():
            raise RuntimeError("MotionLab network worker did not stop cleanly")
        _WORKER = None
    if bpy.app.timers.is_registered(_process_queue):
        bpy.app.timers.unregister(_process_queue)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
