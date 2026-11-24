import pytest

from motionlab_blender.protocol import ProtocolError, validate_job


def valid_job():
    return {
        "id": "job-001",
        "command": "create_cube",
        "args": {"name": "CI Cube", "size": 2, "location": [0, 0, 0]},
        "authorization": {"approved": True, "scopes": ["scene.write"]},
    }


def test_valid_cube_job_is_normalized():
    job = validate_job(valid_job())
    assert job["command"] == "create_cube"
    assert job["args"]["size"] == 2.0
    assert job["args"]["location"] == [0.0, 0.0, 0.0]


@pytest.mark.parametrize("bad_job", [
    None,
    {},
    {"id": "job", "command": "run_python", "args": {}, "authorization": {}},
    {"id": "job", "command": "create_cube", "args": [], "authorization": {}},
])
def test_rejects_malformed_or_unsupported_jobs(bad_job):
    with pytest.raises(ProtocolError):
        validate_job(bad_job)


@pytest.mark.parametrize("size", [True, 0, -1, 1001, float("inf"), float("nan")])
def test_rejects_invalid_cube_sizes(size):
    job = valid_job()
    job["args"]["size"] = size
    with pytest.raises(ProtocolError):
        validate_job(job)


def test_rejects_out_of_range_coordinates():
    job = valid_job()
    job["args"]["location"] = [0, 0, 100001]
    with pytest.raises(ProtocolError):
        validate_job(job)
