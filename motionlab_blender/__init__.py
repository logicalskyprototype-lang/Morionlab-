"""MotionLab Blender Bridge add-on."""

bl_info = {
    "name": "MotionLab Bridge",
    "author": "LogicalSky",
    "version": (0, 1, 0),
    "blender": (3, 6, 0),
    "location": "Edit > Preferences > Add-ons",
    "description": "Controlled outbound connection for approved MotionLab scene jobs",
    "category": "System",
}

import bpy

from .command_queue import CommandQueue
from .connector import ConnectorClient, NetworkWorker
from .permissions import authorize_job
from .protocol import ProtocolError
from .commands.objects import execute_command

_COMMANDS = CommandQueue()
_WORKER = None


def _process_queue():
    global _WORKER
    while True:
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
        except (ProtocolError, RuntimeError, ValueError) as exc:
            result["error"] = str(exc)
        finally:
            if _WORKER is not None:
                _WORKER.submit_result(job["id"], result)
            _COMMANDS.task_done()
    return 0.25


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

        if _WORKER is not None:
            _WORKER.stop()
        _WORKER = NetworkWorker(client, _COMMANDS.put)
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
        _WORKER.stop()
        _WORKER = None
    if bpy.app.timers.is_registered(_process_queue):
        bpy.app.timers.unregister(_process_queue)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
