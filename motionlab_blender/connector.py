"""Outbound-only HTTPS client. Network operations never touch Blender data."""

from __future__ import annotations

import json
import queue
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .protocol import ProtocolError, validate_job


MAX_RESPONSE_BYTES = 256 * 1024


class ConnectorError(RuntimeError):
    pass


class ConnectorClient:
    def __init__(self, base_url, token, timeout=10):
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("MotionLab service URL must use HTTPS.")
        if not token or not isinstance(token, str):
            raise ValueError("A non-empty access token is required.")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(self, method, path, payload=None):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "MotionLab-Blender-Bridge/0.1",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except (HTTPError, URLError, TimeoutError) as exc:
            raise ConnectorError("MotionLab service request failed.") from exc

        if len(raw) > MAX_RESPONSE_BYTES:
            raise ConnectorError("MotionLab service response is too large.")
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConnectorError("MotionLab service returned invalid JSON.") from exc

    def fetch_next_job(self):
        payload = self._request("GET", "/v1/jobs/next")
        if payload is None or payload.get("job") is None:
            return None
        try:
            return validate_job(payload["job"])
        except (AttributeError, ProtocolError) as exc:
            raise ConnectorError("MotionLab service returned an invalid job.") from exc

    def submit_result(self, job_id, result):
        return self._request(
            "POST",
            "/v1/jobs/{}/result".format(job_id),
            result,
        )


class NetworkWorker:
    """Polls the service and submits results without accessing bpy."""

    def __init__(self, client, on_job, poll_interval=5.0):
        self.client = client
        self.on_job = on_job
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._results = queue.Queue()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="MotionLabNetworkWorker",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout=2.0):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def submit_result(self, job_id, result):
        self._results.put((job_id, result))

    def _run(self):
        while not self._stop.is_set():
            self._flush_results()
            try:
                job = self.client.fetch_next_job()
                if job is not None:
                    self.on_job(job)
            except ConnectorError:
                # Retry on the next poll. The add-on can expose status in a later UI iteration.
                pass
            self._stop.wait(self.poll_interval)

        self._flush_results()

    def _flush_results(self):
        while True:
            try:
                job_id, result = self._results.get_nowait()
            except queue.Empty:
                return
            try:
                self.client.submit_result(job_id, result)
            except ConnectorError:
                # Do not silently discard results on transient failures.
                self._results.put((job_id, result))
                return
            finally:
                self._results.task_done()
