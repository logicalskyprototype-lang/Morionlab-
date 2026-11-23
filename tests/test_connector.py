import pytest

from motionlab_blender.connector import ConnectorClient


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



def test_redirect_handler_refuses_redirects():
    from motionlab_blender.connector import _NoRedirectHandler

    handler = _NoRedirectHandler()
    assert handler.redirect_request(
        None, None, 302, "Found", {}, "https://other.example/"
    ) is None
