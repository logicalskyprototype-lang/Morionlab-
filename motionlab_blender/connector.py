"""Outbound-only HTTPS client. Network operations never touch Blender data."""

from __future__ import annotations

import json
import queue
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .protocol import ProtocolError, validate_job


MAX_RESPONSE_BYTES = 256 * 1024
MAX_PENDING_RESULTS = 100
DEFAULT_REQUEST_TIMEOUT = 2.0


class _NoRedirectHandler(HTTPRedirectHandler):
    """Do not forward bearer credentials through HTTP redirects."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ConnectorError(RuntimeError):
    pass


class ConnectorClient:
    def __init__(self, base_url, token, timeout=DEFAULT_REQUEST_TIMEOUT):
        parsed = urlparse(base_url)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("MotionLab service URL must be a clean HTTPS URL.")
        if not isinstance(token, str) or not token.strip():
            raise ValueError("A non-empty access token is required.")
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError("Request timeout must be a positive number.")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = float(timeout)

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
            with build_opener(_NoRedirectHandler).open(request, timeout=self.timeout) as response:
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
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise ConnectorError("MotionLab service returned an invalid response.")
        raw_job = payload.get("job")
        if raw_job is None:
            return None
        try:
            return validate_job(raw_job)
        except ProtocolError as exc:
            raise ConnectorError("MotionLab service returned an invalid job.") from exc

    def submit_result(self, job_id, result):
        # validate_job constrains IDs to URL-safe characters before they reach this path.
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
        self._results = queue.Queue(maxsize=MAX_PENDING_RESULTS)
        self._retry_result = None
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

    def stop(self, timeout=5.0):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            return not self._thread.is_alive()
        return True

    def can_accept_result(self):
        # A failed submission is held separately and blocks further scene work.
        return self._retry_result is None and not self._results.full()

    def submit_result(self, job_id, result):
        try:
            self._results.put_nowait((job_id, result))
            return True
        except queue.Full:
            return False

    def _run(self):
        while not self._stop.is_set():
            self._flush_results()
            if self._stop.is_set():
                break

            # Stop accepting work while result delivery is backlogged.
            if self.can_accept_result():
                try:
                    job = self.client.fetch_next_job()
                    if job is not None:
                        self.on_job(job)
                except ConnectorError:
                    # Retry on the next poll. UI status reporting is a later increment.
                    pass

            self._stop.wait(self.poll_interval)

    def _flush_results(self):
        while not self._stop.is_set():
            if self._retry_result is not None:
                item = self._retry_result
                self._retry_result = None
            else:
                try:
                    item = self._results.get_nowait()
                except queue.Empty:
                    return

            job_id, result = item
            try:
                self.client.submit_result(job_id, result)
            except ConnectorError:
                # Retain the failed item without racing a concurrent queue producer.
                self._retry_result = item
                if item is not None and self._results.unfinished_tasks:
                    # Balance task accounting only for items removed from the queue.
                    pass
                return
            else:
                if self._retry_result is None and self._results.unfinished_tasks:
                    # Queue task accounting is informational; no join() is used.
                    pass
