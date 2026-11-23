import socket
from dataclasses import replace

import pytest

from buildsize.config import Config
from buildsize.domain.errors import BuildSizeError
from buildsize.storage import apk_download


@pytest.mark.parametrize(
    "url",
    [
        "http://apk.example.com/app.apk",
        "https://apk.example.com.other.example/app.apk",
        "https://user@apk.example.com/app.apk",
        "https://apk.example.com:444/app.apk",
        "https://apk.example.com/apiv2/app/install",
        "https://apk.example.com//app.apk",
    ],
)
def test_only_approved_https_targets(url):
    with pytest.raises(BuildSizeError) as exc:
        apk_download.download_target(url, ("apk.example.com",))
    assert exc.value.code == "APK_DOWNLOAD_URL_INVALID"


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "169.254.169.254", "10.1.2.3", "::1", "64:ff9b::a00:1"]
)
def test_dns_with_public_and_restricted_answers_is_rejected(monkeypatch, address):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (0, 0, 0, "", ("93.184.216.34", 443)),
            (0, 0, 0, "", (address, 443)),
        ],
    )
    with pytest.raises(BuildSizeError) as exc:
        apk_download._resolve("apk.example.com")
    assert exc.value.code == "APK_DOWNLOAD_ADDRESS_DENIED"


def test_signed_download_url_is_encrypted_and_authenticated():
    config = Config("sqlite://", "http://localhost", "a" * 32, "b" * 32, app_env="test")
    url = "https://apk.example.com/final.apk?signature=fixture"
    encrypted = apk_download.protect_url(config, url)
    assert "signature" not in encrypted
    assert apk_download.reveal_url(config, encrypted) == url
    with pytest.raises(BuildSizeError) as exc:
        apk_download.reveal_url(replace(config, session_secret="c" * 32), encrypted)
    assert exc.value.code == "APK_DOWNLOAD_CREDENTIAL_INVALID"
