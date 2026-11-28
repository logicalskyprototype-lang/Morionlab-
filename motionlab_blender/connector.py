"""Outbound-only HTTPS client. Network operations never touch Blender data."""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
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


class LeaseLostError(ConnectorError):
    """The server rejected an operation because its job lease was fenced."""


class ResultConflictError(ConnectorError):
    """The server already accepted a different terminal result for this job."""


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

    @staticmethod
    def _http_error_detail(exc):
        try:
            raw = exc.read(MAX_RESPONSE_BYTES + 1)
        except OSError:
            return None
        if not raw or len(raw) > MAX_RESPONSE_BYTES:
            return None
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        detail = body.get("detail") if isinstance(body, dict) else None
        return detail if isinstance(detail, str) else None

    def _request(self, method, path, payload=None, conflict_kind=None):
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
        except HTTPError as exc:
            if exc.code == 409:
                detail = self._http_error_detail(exc)
                if conflict_kind == "lease":
                    raise LeaseLostError(
                        detail or "MotionLab job lease is no longer valid."
                    ) from exc
                if conflict_kind == "result":
                    lease_errors = {
                        "Lease token is no longer valid for this job.",
                        "Job is not currently leased.",
                        "Job lease is no longer active for this installation.",
                    }
                    if detail in lease_errors:
                        raise LeaseLostError(detail) from exc
                    raise ResultConflictError(
                        detail or "MotionLab rejected a conflicting result (HTTP 409)."
                    ) from exc
                raise ConnectorError(
                    detail or "MotionLab service returned HTTP 409."
                ) from exc
            raise ConnectorError("MotionLab service request failed.") from exc
        except (URLError, TimeoutError) as exc:
            raise ConnectorError("MotionLab service request failed.") from exc

        if len(raw) > MAX_RESPONSE_BYTES:
            raise ConnectorError("MotionLab service response is too large.")
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConnectorError("MotionLab service returned invalid JSON.") from exc

    def heartbeat(self, job_id=None, lease_token=None):
        payload = {}
        if job_id is not None or lease_token is not None:
            if not job_id or not lease_token:
                raise ValueError("job_id and lease_token must be supplied together.")
            payload = {"job_id": job_id, "lease_token": lease_token}
        response = self._request("POST", "/v1/worker/heartbeat", payload, conflict_kind="lease")
        if not isinstance(response, dict) or response.get("ok") is not True:
            raise ConnectorError("MotionLab service returned an invalid heartbeat response.")
        return response

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
            conflict_kind="result",
        )


class NetworkWorker:
    """Polls the service and submits results without accessing bpy."""

    def __init__(self, client, on_job, poll_interval=5.0, heartbeat_interval=15.0):
        if poll_interval <= 0 or heartbeat_interval <= 0:
            raise ValueError("Worker intervals must be positive.")
        self.client = client
        self.on_job = on_job
        self.poll_interval = float(poll_interval)
        self.heartbeat_interval = float(heartbeat_interval)
        self._stop = threading.Event()
        self._results = queue.Queue(maxsize=MAX_PENDING_RESULTS)
        self._retry_result = None
        self._active_job = None
        self._last_heartbeat = 0.0
        self._result_conflicts = []
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
        # A failed submission or result conflict blocks further scene work.
        return (
            not self._stop.is_set()
            and not self._result_conflicts
            and self._retry_result is None
            and not self._results.full()
        )

    def submit_result(self, job_id, result):
        if self._stop.is_set() or self._result_conflicts:
            return False
        try:
            self._results.put_nowait((job_id, result))
            return True
        except queue.Full:
            return False

    def _run(self):
        while True:
            self._flush_results()
            if self._stop.is_set():
                # Drain already-saved results before shutting down. If the lease
                # expires first, redelivery is safe because the .blend ledger is durable.
                if self._retry_result is None and self._results.empty():
                    return
                time.sleep(min(self.poll_interval, self.heartbeat_interval))
                continue

            now = time.monotonic()
            if now - self._last_heartbeat >= self.heartbeat_interval:
                try:
                    if self._active_job is None:
                        self.client.heartbeat()
                    else:
                        self.client.heartbeat(
                            self._active_job["id"],
                            self._active_job["lease_token"],
                        )
                    self._last_heartbeat = now
                except LeaseLostError:
                    # The job has been fenced and may be redelivered. The Blender
                    # project ledger makes replay safe by job ID.
                    self._active_job = None
                    self._last_heartbeat = 0.0
                except ConnectorError:
                    # The next cycle retries. The server expires the lease if the
                    # worker cannot renew it, preventing a permanently stuck job.
                    pass

            # Stop accepting work while result delivery is backlogged.
            if self.can_accept_result() and self._active_job is None:
                try:
                    job = self.client.fetch_next_job()
                    if job is not None and not self._stop.is_set():
                        # Register the lease before handing the job to Blender's
                        # main-thread queue so a fast result cannot beat this state update.
                        self._active_job = job
                        try:
                            self.on_job(job)
                        except ConnectorError:
                            self._active_job = None
                            self._last_heartbeat = 0.0
                            raise
                except ConnectorError:
                    # Retry on the next poll.
                    pass

            if not self._stop.is_set():
                self._stop.wait(min(self.poll_interval, self.heartbeat_interval))

    def _flush_results(self):
        # Also runs during graceful shutdown so queued and retry-held results drain.
        while True:
            from_queue = self._retry_result is None
            if from_queue:
                try:
                    item = self._results.get_nowait()
                except queue.Empty:
                    return
            else:
                item = self._retry_result
                self._retry_result = None

            job_id, result = item
            try:
                self.client.submit_result(job_id, result)
            except LeaseLostError:
                # A fenced result cannot ever be accepted with its old token.
                # Drop it and allow the server to redeliver; Blender's saved
                # job ledger prevents duplicate scene mutations on replay.
                if self._active_job is not None and self._active_job["id"] == job_id:
                    self._active_job = None
                    self._last_heartbeat = 0.0
                if from_queue:
                    self._results.task_done()
                return
            except ResultConflictError as exc:
                # A different result was accepted; this is an integrity conflict,
                # not a stale lease. Retain and log it instead of silently dropping it.
                self._result_conflicts.append((job_id, result, str(exc)))
                logging.getLogger(__name__).error(
                    "MotionLab result conflict for job %s: %s", job_id, exc
                )
                if self._active_job is not None and self._active_job["id"] == job_id:
                    self._active_job = None
                    self._last_heartbeat = 0.0
                if from_queue:
                    self._results.task_done()
                continue
            except ConnectorError:
                # Retain the failed item without racing a concurrent queue producer.
                self._retry_result = item
                if from_queue:
                    self._results.task_done()
                return
            else:
                if self._active_job is not None and self._active_job["id"] == job_id:
                    self._active_job = None
                    self._last_heartbeat = 0.0
                if from_queue:
                    self._results.task_done()
