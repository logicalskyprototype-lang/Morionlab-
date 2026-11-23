"""Bounded, thread-safe handoff to Blender's main thread."""

from queue import Empty, Full, Queue
from typing import Any, Dict, Optional


class CommandQueue:
    def __init__(self, maxsize: int = 100) -> None:
        if maxsize < 1:
            raise ValueError("Command queue maxsize must be positive.")
        self._queue: Queue = Queue(maxsize=maxsize)

    def put(self, job: Dict[str, Any]) -> bool:
        """Queue a job without blocking the network worker."""
        try:
            self._queue.put_nowait(job)
            return True
        except Full:
            return False

    def get_nowait(self) -> Optional[Dict[str, Any]]:
        try:
            return self._queue.get_nowait()
        except Empty:
            return None

    def task_done(self) -> None:
        self._queue.task_done()

    def empty(self) -> bool:
        return self._queue.empty()
