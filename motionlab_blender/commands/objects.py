"""Small allowlist of scene operations. Called only on Blender's main thread."""

import bpy


def create_cube(args):
    name = args["name"]
    size = args["size"]
    location = args["location"]

    bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    obj = bpy.context.object
    obj.name = name
    return {
        "object_name": obj.name,
        "object_type": obj.type,
        "location": [float(value) for value in obj.location],
        "dimensions": [float(value) for value in obj.dimensions],
    }


def execute_command(job):
    if job["command"] == "create_cube":
        return create_cube(job["args"])
    raise ValueError("Unsupported command.")
