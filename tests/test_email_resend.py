import json
from urllib.request import Request

import pytest

from backend import email_resend


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return b'{"id":"email-test-id"}'


def test_resend_sender_posts_transactional_magic_link(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv(
        "MOTIONLAB_EMAIL_FROM",
        "MotionLab <auth@auth.example.com>",
    )
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(email_resend, "urlopen", fake_urlopen)
    sender = email_resend.build_resend_sender()
    sender("person@example.com", "https://motionlab.example/#auth_token=secret")

    request = captured["request"]
    assert isinstance(request, Request)
    assert request.full_url == email_resend.RESEND_ENDPOINT
    assert request.method == "POST"
    assert request.get_header("Authorization") == "Bearer re_test_key"
    assert captured["timeout"] == 10
    payload = json.loads(request.data)
    assert payload["to"] == ["person@example.com"]
    assert payload["from"] == "MotionLab <auth@auth.example.com>"
    assert "auth_token=secret" in payload["text"]
    assert 'href="https://motionlab.example/#auth_token=secret"' in payload["html"]


def test_resend_sender_fails_closed_without_credentials(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.setenv("MOTIONLAB_EMAIL_FROM", "MotionLab <auth@example.com>")
    sender = email_resend.build_resend_sender()
    with pytest.raises(RuntimeError, match="not configured"):
        sender("person@example.com", "https://motionlab.example/#auth_token=secret")


def test_resend_sender_rejects_header_injection(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("MOTIONLAB_EMAIL_FROM", "MotionLab\r\nBcc: attacker@example.com")
    sender = email_resend.build_resend_sender()
    with pytest.raises(RuntimeError, match="header characters"):
        sender("person@example.com", "https://motionlab.example/#auth_token=secret")
