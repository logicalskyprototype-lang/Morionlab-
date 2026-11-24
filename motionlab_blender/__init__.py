"""MotionLab Blender Bridge add-on package.

Keeping Blender imports lazy allows protocol and permission modules to be tested
with standard CPython without requiring Blender to be installed.
"""

bl_info = {
    "name": "MotionLab Bridge",
    "author": "LogicalSky",
    "version": (0, 1, 0),
    "blender": (3, 6, 0),
    "location": "Edit > Preferences > Add-ons",
    "description": "Controlled outbound connection for approved MotionLab scene jobs",
    "category": "System",
}


def register():
    from .addon import register as register_addon
    register_addon()


def unregister():
    from .addon import unregister as unregister_addon
    unregister_addon()
