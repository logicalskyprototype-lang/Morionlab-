import io
import json
import threading

import pytest

from motionlab_blender.connector import (
    MAX_PENDING_RESULTS,
    ConnectorError,
    ConnectorClient,
    LeaseLostError,
    NetworkWorker,
    ResultConflictError,
)


def test_requires_https_service_url():
    with pytest.raises(ValueError):
        ConnectorClient("http://motionlab.example", "token")


@pytest.mark.parametrize("url", [
    "https://",
    "https://user:pass@motionlab.example",
    "https://motionlab.example/path?token=leak",
    "https://motionlab.example/path#fragment",
])
def test_rejects_ambiguous_or_credential_bearing_urls(url):
    with pytest.raises(ValueError):
        ConnectorClient(url, "token")


def test_requires_non_empty_token():
    with pytest.raises(ValueError):
        ConnectorClient("https://motionlab.example", " ")


def test_request_timeout_must_be_positive():
    with pytest.raises(ValueError):
        ConnectorClient("https://motionlab.example", "token", timeout=0)


def test_redirect_handler_refuses_redirects():
    from motionlab_blender.connector import _NoRedirectHandler

    handler = _NoRedirectHandler()
    assert handler.redirect_request(
        None, None, 302, "Found", {}, "https://other.example/"
    ) is None


def _mock_http_409(monkeypatch, detail):
    from urllib.error import HTTPError

    class _FailingOpener:
        def open(self, request, timeout):
            body = json.dumps({"detail": detail}).encode("utf-8")
            raise HTTPError(
                request.full_url,
                409,
                "Conflict",
                {},
                io.BytesIO(body),
            )

    monkeypatch.setattr(
        "motionlab_blender.connector.build_opener",
        lambda *_args: _FailingOpener(),
    )


def test_result_409_lease_failures_are_classified_as_lease_lost(monkeypatch):
    _mock_http_409(monkeypatch, "Job lease is no longer active for this installation.")
    client = ConnectorClient("https://motionlab.example", "token")

    with pytest.raises(LeaseLostError):
        client.submit_result("job-1", {"job_id": "job-1"})


def test_result_409_conflicting_result_is_not_classified_as_lease_lost(monkeypatch):
    _mock_http_409(monkeypatch, "A different result was already accepted for this job.")
    client = ConnectorClient("https://motionlab.example", "token")

    with pytest.raises(ResultConflictError, match="different result"):
        client.submit_result("job-1", {"job_id": "job-1"})


def test_unrelated_409_is_not_classified_as_lease_lost(monkeypatch):
    _mock_http_409(monkeypatch, "Unexpected conflict.")
    client = ConnectorClient("https://motionlab.example", "token")

    with pytest.raises(ConnectorError, match="Unexpected conflict"):
        client.fetch_next_job()


def test_worker_result_queue_is_bounded():
    client = ConnectorClient("https://motionlab.example", "token")
    worker = NetworkWorker(client, lambda job: None)

    assert worker.can_accept_result()
    for index in range(MAX_PENDING_RESULTS):
        assert worker.submit_result("job-{}".format(index), {"status": "completed"})
    assert not worker.can_accept_result()
    assert worker.submit_result("overflow", {"status": "completed"}) is False


class _FlakyResultClient:
    def __init__(self):
        self.attempts = []

    def submit_result(self, job_id, result):
        self.attempts.append((job_id, result))
        if len(self.attempts) == 1:
            from motionlab_blender.connector import ConnectorError
            raise ConnectorError("temporary failure")
        return {"accepted": True}


def test_failed_result_submission_is_retained_and_retried():
    client = _FlakyResultClient()
    worker = NetworkWorker(client, lambda job: None)
    result = {"status": "completed"}

    assert worker.submit_result("job-retry", result) is True
    worker._flush_results()

    # Failed delivery must block more scene work until retry succeeds.
    assert worker.can_accept_result() is False
    worker._flush_results()

    assert len(client.attempts) == 2
    assert client.attempts[0] == ("job-retry", result)
    assert client.attempts[1] == ("job-retry", result)
    assert worker.can_accept_result() is True



def test_worker_shutdown_drains_a_transiently_failed_result():
    class _FlakyClient:
        def __init__(self):
            self.attempts = []

        def heartbeat(self):
            return {"ok": True}

        def fetch_next_job(self):
            return None

        def submit_result(self, job_id, result):
            self.attempts.append((job_id, result))
            if len(self.attempts) == 1:
                raise ConnectorError("temporary network failure")
            return {"accepted": True}

    client = _FlakyClient()
    worker = NetworkWorker(
        client,
        lambda job: None,
        poll_interval=0.01,
        heartbeat_interval=0.01,
    )
    result = {"status": "completed"}
    assert worker.submit_result("job-stop", result)

    worker.start()
    assert worker.stop(timeout=1.0) is True
    assert client.attempts == [("job-stop", result), ("job-stop", result)]


def test_worker_registers_active_job_before_callback_and_flushes_fast_result():
    job = {"id": "job-fast", "lease_token": "lease-fast"}
    submitted = threading.Event()
    observed_active = []

    class _ImmediateClient:
        def __init__(self):
            self.fetched = False

        def heartbeat(self):
            return {"ok": True}

        def fetch_next_job(self):
            if self.fetched:
                return None
            self.fetched = True
            return job

        def submit_result(self, job_id, result):
            assert job_id == job["id"]
            submitted.set()
            return {"accepted": True}

    worker_ref = {}

    def on_job(received):
        worker = worker_ref["worker"]
        observed_active.append(worker._active_job is received)
        assert worker.submit_result(received["id"], {"status": "completed"})

    worker = NetworkWorker(
        _ImmediateClient(),
        on_job,
        poll_interval=0.01,
        heartbeat_interval=0.01,
    )
    worker_ref["worker"] = worker
    worker.start()

    assert submitted.wait(1.0)
    assert worker.stop(timeout=1.0) is True
    assert observed_active == [True]
    assert worker._active_job is None


def test_result_conflict_is_retained_and_blocks_new_scene_work(caplog):
    class _ConflictingClient:
        def submit_result(self, job_id, result):
            raise ResultConflictError("A different result was already accepted for this job.")

    worker = NetworkWorker(_ConflictingClient(), lambda job: None)
    worker._active_job = {"id": "job-conflict", "lease_token": "lease-conflict"}
    result = {"status": "completed"}
    assert worker.submit_result("job-conflict", result)

    worker._flush_results()

    assert len(worker._result_conflicts) == 1
    assert worker._result_conflicts[0][0:2] == ("job-conflict", result)
    assert worker._active_job is None
    assert worker.can_accept_result() is False
    assert "MotionLab result conflict" in caplog.text


def test_connector_heartbeat_sends_idle_and_active_payloads():
    client = ConnectorClient("https://motionlab.example", "token")
    calls = []
    client._request = lambda method, path, payload=None, **_kwargs: (
        calls.append((method, path, payload)) or {"ok": True}
    )

    assert client.heartbeat() == {"ok": True}
    assert client.heartbeat("job-1", "lease-1") == {"ok": True}
    assert calls == [
        ("POST", "/v1/worker/heartbeat", {}),
        ("POST", "/v1/worker/heartbeat", {"job_id": "job-1", "lease_token": "lease-1"}),
    ]


def test_connector_heartbeat_requires_job_id_and_lease_token_together():
    client = ConnectorClient("https://motionlab.example", "token")
    with pytest.raises(ValueError):
        client.heartbeat("job-1", None)
    with pytest.raises(ValueError):
        client.heartbeat(None, "lease-1")


def test_fenced_result_is_discarded_so_job_can_be_redelivered():
    from motionlab_blender.connector import LeaseLostError

    class FencedClient:
        def submit_result(self, job_id, result):
            raise LeaseLostError("lease expired")

    worker = NetworkWorker(FencedClient(), lambda job: None)
    worker._active_job = {"id": "job-fenced", "lease_token": "old-token"}
    assert worker.submit_result("job-fenced", {"status": "completed"})
    worker._flush_results()

    assert worker._active_job is None
    assert worker.can_accept_result() is True
