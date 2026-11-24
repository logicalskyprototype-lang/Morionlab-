from motionlab_blender.command_queue import CommandQueue


def test_queue_accepts_jobs_until_capacity():
    pending = CommandQueue(maxsize=2)
    assert pending.put({"id": "one"}) is True
    assert pending.put({"id": "two"}) is True
    assert pending.put({"id": "three"}) is False


def test_queue_returns_jobs_in_fifo_order():
    pending = CommandQueue(maxsize=2)
    pending.put({"id": "one"})
    pending.put({"id": "two"})
    first = pending.get_nowait()
    assert first == {"id": "one"}
    pending.task_done()
    assert pending.get_nowait() == {"id": "two"}
    pending.task_done()


def test_queue_can_be_reused_after_full_queue_is_drained():
    pending = CommandQueue(maxsize=1)
    assert pending.put({"id": "one"}) is True
    assert pending.put({"id": "two"}) is False
    pending.get_nowait()
    pending.task_done()
    assert pending.put({"id": "two"}) is True
