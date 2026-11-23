"""Thread-safe handoff from the network worker to Blender's main thread."""

from queue import Empty, Queue
from typing import Any, Dict, Optional


class CommandQueue:
    def __init__(self) -> None:
        self._queue: Queue = Queue()

    def put(self, job: Dict[str, Any]) -> None:
        self._queue.put(job)

    def get_nowait(self) -> Optional[Dict[str, Any]]:
        try:
            return self._queue.get_nowait()
        except Empty:
            return None

    def task_done(self) -> None:
        self._queue.task_done()

    def empty(self) -> bool:
        return self._queue.empty()
