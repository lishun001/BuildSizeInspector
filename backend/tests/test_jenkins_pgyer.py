import http.client
import json
import os
import runpy
import shutil
import subprocess
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

JENKINS = Path(__file__).resolve().parents[2] / "integrations/jenkins"
SIGNED_URL = "https://cos3.pgyer.com/apps/build.apk?signature=fixture%3Dvalue"


@pytest.mark.parametrize(
    ("status", "location", "error_message"),
    [
        (302, SIGNED_URL, None),
        (302, "https://pgyer-apps-cfw.appmeme.com/apps/build.apk?signature=fixture", None),
        (302, "http://cos3.pgyer.com/apps/build.apk", "缺少 HTTPS 协议"),
        (302, "https://www.pgyer.com/install-page", "目标路径缺少 APK 后缀"),
        (302, "https://elsewhere.example.invalid/apps/build.apk", "不在已知的蒲公英文件域名中"),
        (302, "https://other.appmeme.com/apps/build.apk", "不在已知的蒲公英文件域名中"),
        (302, "https://cos3.pgyer.com/apps/build.apk?_api_key=fixture-secret", "地址含账户凭据"),
        (200, None, "业务码 1006"),
    ],
)
def test_pgyer_url_uses_published_build_and_only_persists_file_link(
    tmp_path, monkeypatch, status, location, error_message
):
    handoff = runpy.run_path(str(JENKINS / "unity-report-handoff.py"))
    published = tmp_path / "pgyer_upload.log"
    published.write_text(
        'Full response JSON:\n{"code":0,"data":{"buildKey":"this-build",'
        '"buildShortcutUrl":"channel"}}\n'
    )
    output = tmp_path / "download-url.txt"
    monkeypatch.setenv("PGY_API_KEY", "fixture-secret")

    def request(method, path, **_):
        assert method == "GET"
        assert urlsplit(path).path == "/apiv2/app/install"
        assert parse_qs(urlsplit(path).query) == {
            "_api_key": ["fixture-secret"],
            "buildKey": ["this-build"],
        }

    def connect(host, **_):
        assert host == "www.pgyer.com"
        response = SimpleNamespace(
            status=status,
            getheader=lambda name: location if name == "Location" else None,
            read=lambda limit: b'{"code":1006,"message":"fixture-secret"}'[:limit],
        )
        return SimpleNamespace(
            request=request, getresponse=lambda: nullcontext(response), close=lambda: None
        )

    monkeypatch.setattr(http.client, "HTTPSConnection", connect)
    if error_message:
        with pytest.raises(handoff["HandoffError"], match=error_message) as error:
            handoff["pgyer_url"](published, output)
        assert "fixture-secret" not in str(error.value)
        assert not output.exists()
    else:
        assert handoff["pgyer_url"](published, output) == "this-build"
        assert output.read_text() == location + "\n"
        assert output.stat().st_mode & 0o777 == 0o600


def test_pgyer_shell_passes_file_link_to_submit_and_cleans_it(tmp_path):
    workspace = tmp_path / "workspace with spaces"
    scripts = workspace / "ci"
    scripts.mkdir(parents=True)
    shutil.copyfile(JENKINS / "submit-pgyer.sh", scripts / "submit-pgyer.sh")
    (scripts / "unity-report-handoff.py").write_text(
        """import json, os, sys
from pathlib import Path

def argument(name):
    return sys.argv[sys.argv.index(name) + 1]

if sys.argv[1] == "pgyer-url":
    assert os.environ["PGY_API_KEY"] == "fixture-secret"
    assert "SSL_CERT_FILE" not in os.environ
    assert Path(argument("--publish-log")).is_file()
    Path(argument("--output")).write_text("https://cos3.pgyer.com/build.apk?signature=fixture")
    print("this-build")
elif sys.argv[1] == "submit":
    assert os.environ["SSL_CERT_FILE"] == "buildsize-private-ca"
    assert argument("--pgyer-build-key") == "this-build"
    assert Path(argument("--apk")).name == "final signed.apk"
    link = Path(argument("--apk-url-file"))
    assert link.read_text() == "https://cos3.pgyer.com/build.apk?signature=fixture"
    Path(argument("--receipt")).write_text(json.dumps({"build_key": "this-build"}))
"""
    )
    (workspace / ".env.sh").write_text("export PGY_API_KEY=fixture-secret\n")
    publish = workspace / ".jenkins"
    publish.mkdir()
    (publish / "pgyer_upload.log").write_text("fixture")
    context = workspace / "context directory"
    context.mkdir()
    (context / "context.json").write_text("fixture")
    (context / "completed-at.txt").write_text("fixture")
    apks = workspace / "APK directory"
    apks.mkdir()
    (apks / "final signed.apk").write_bytes(b"fixture")
    run = subprocess.run(
        ["bash", scripts / "submit-pgyer.sh", context, apks],
        env={**os.environ, "WORKSPACE": str(workspace), "SSL_CERT_FILE": "buildsize-private-ca"},
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert "等待后台下载和分析" in run.stdout
    assert "fixture-secret" not in run.stdout + run.stderr
    assert "signature=fixture" not in run.stdout + run.stderr
    assert not list(context.glob(".apk-download-url.*"))
    assert json.loads((context / "receipt.json").read_text()) == {"build_key": "this-build"}
