"""Blender registration, save-gated result delivery and main-thread execution."""

import bpy
from bpy.app.handlers import persistent

from .command_queue import CommandQueue
from .connector import ConnectorClient, NetworkWorker, MAX_PENDING_RESULTS
from .permissions import authorize_job
from .commands.objects import execute_command

_COMMANDS = CommandQueue(maxsize=100)
_WORKER = None
_MAX_JOBS_PER_TICK = 10
_PENDING_RESULTS = {}
_SAVED_RESULTS = {}


@persistent
def _on_save_post(_unused):
    """Only results included in a completed .blend save may be acknowledged."""
    _SAVED_RESULTS.update(_PENDING_RESULTS)
    _PENDING_RESULTS.clear()


def _flush_saved_results():
    """Move results from the post-save buffer to the network worker."""
    if _WORKER is None:
        return 0.25
    for job_id, result in list(_SAVED_RESULTS.items()):
        if not _WORKER.can_accept_result():
            break
        if _WORKER.submit_result(job_id, result):
            _SAVED_RESULTS.pop(job_id, None)
        else:
            break
    return 0.25


def _process_queue():
    processed = 0

    # Never acknowledge scene changes from an untitled/unsaved Blender project.
    # Jobs remain queued until the user saves the project at least once.
    if not bpy.data.filepath:
        return 1.0

    while processed < _MAX_JOBS_PER_TICK:
        if len(_PENDING_RESULTS) + len(_SAVED_RESULTS) >= MAX_PENDING_RESULTS:
            break
        if _WORKER is not None and not _WORKER.can_accept_result():
            break
        job = _COMMANDS.get_nowait()
        if job is None:
            break

        result = {"job_id": job["id"], "lease_token": job["lease_token"], "status": "failed"}
        try:
            authorize_job(job)
            result.update({
                "status": "completed",
                "output": execute_command(job),
            })
        except Exception as exc:
            result["error"] = str(exc)
        finally:
            # Do not send this result until save_post confirms the ledger and
            # scene mutation were included in a successful .blend save.
            _PENDING_RESULTS[job["id"]] = result
            _COMMANDS.task_done()
        processed += 1

    return 0.01 if not _COMMANDS.empty() else 0.25


def _enqueue_job(job):
    if not _COMMANDS.put(job):
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
        if not bpy.app.timers.is_registered(_flush_saved_results):
            bpy.app.timers.register(_flush_saved_results, first_interval=0.25)
        self.report({"INFO"}, "MotionLab connection worker started")
        return {"FINISHED"}


_CLASSES = (
    MOTIONLAB_Preferences,
    MOTIONLAB_OT_Connect,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    if _on_save_post not in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.append(_on_save_post)


def unregister():
    global _WORKER
    if _WORKER is not None:
        if not _WORKER.stop():
            raise RuntimeError("MotionLab network worker did not stop cleanly")
        _WORKER = None
    for timer in (_process_queue, _flush_saved_results):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    if _on_save_post in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.remove(_on_save_post)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
