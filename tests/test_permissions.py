import pytest

from motionlab_blender.permissions import authorize_job
from motionlab_blender.protocol import ProtocolError


def test_allows_explicitly_approved_scene_write_job():
    authorize_job({
        "authorization": {"approved": True, "scopes": ["scene.write"]}
    })


@pytest.mark.parametrize("authorization", [
    None,
    {},
    {"approved": False, "scopes": ["scene.write"]},
    {"approved": True, "scopes": []},
    {"approved": True, "scopes": "scene.write"},
])
def test_rejects_missing_or_insufficient_approval(authorization):
    with pytest.raises(ProtocolError):
        authorize_job({"authorization": authorization})
