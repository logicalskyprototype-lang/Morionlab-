import pytest

from motionlab_blender.connector import (
    MAX_PENDING_RESULTS,
    ConnectorClient,
    NetworkWorker,
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



def test_connector_heartbeat_sends_idle_and_active_payloads():
    client = ConnectorClient("https://motionlab.example", "token")
    calls = []
    client._request = lambda method, path, payload=None: (
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
