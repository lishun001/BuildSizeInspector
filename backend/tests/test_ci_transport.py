import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from buildsize.client.files import ClientError
from buildsize.client.transport import ApiClient


@contextmanager
def endpoint(statuses, *, redirect=None):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            self.do_POST()

        def do_PUT(self):
            self.do_POST()

        def do_POST(self):
            data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            requests.append((self.path, data, self.headers.get("Authorization")))
            code = statuses[min(len(requests) - 1, len(statuses) - 1)]
            payload = json.dumps({"ok": True}).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Retry-After", "0")
            if redirect:
                self.send_header("Location", redirect)
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_retry_reopens_full_file_and_stops_after_three(tmp_path: Path) -> None:
    content = b"large-test-fixture" * 10000
    path = tmp_path / "file with spaces.apk"
    path.write_bytes(content)
    with endpoint([503, 429, 201]) as (url, requests):
        client = ApiClient(url, "temporary-fixture-token", timeout=5)
        status, result = client.request("PUT", "/upload", file=path)
        assert status == 201 and result["ok"]
        assert len(requests) == 3
        assert all(row[1] == content for row in requests)
    with endpoint([503]) as (url, requests):
        with pytest.raises(ClientError) as error:
            ApiClient(url, "temporary-fixture-token", timeout=5).request("POST", "/x", body={})
        assert error.value.exit_code == 4
        assert len(requests) == 3


@pytest.mark.parametrize(
    "code,exit_code", [(401, 3), (403, 3), (409, 5), (413, 2), (422, 5), (307, 3)]
)
def test_non_retry_errors_and_redirects_never_forward_token(code: int, exit_code: int) -> None:
    with endpoint([200]) as (other, received):
        with endpoint([code], redirect=other + "/steal") as (url, requests):
            with pytest.raises(ClientError) as error:
                ApiClient(url, "sensitive-fixture-token", timeout=3).request("GET", "/status")
            assert error.value.exit_code == exit_code
            assert "sensitive-fixture-token" not in str(error.value)
            assert len(requests) == 1
        assert received == []


@pytest.mark.parametrize(
    "url",
    [
        "http://external.invalid",
        "https://name:secret@example.invalid",
        "https://example.invalid?token=secret",
    ],
)
def test_insecure_api_configuration_rejected(url: str) -> None:
    with pytest.raises(ClientError) as error:
        ApiClient(url, "token")
    assert error.value.exit_code == 3


@pytest.mark.parametrize("signal_name,expected", [("SIGINT", 130), ("SIGTERM", 143)])
def test_cli_interrupt_keeps_nonzero_exit(tmp_path: Path, signal_name: str, expected: int) -> None:
    import os
    import signal
    import subprocess
    import sys
    import uuid

    reached, release = threading.Event(), threading.Event()

    class Waiting(BaseHTTPRequestHandler):
        def do_GET(self):
            reached.set()
            release.wait(5)
            self.close_connection = True

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Waiting)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        json.dumps({"api_url": url, "build_id": str(uuid.uuid4()), "run_id": str(uuid.uuid4())})
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "buildsize", "status", "--receipt", str(receipt)],
        env={**os.environ, "BUILDSIZE_API_URL": url, "BUILDSIZE_API_TOKEN": "fixture-signal-token"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert reached.wait(5), "CLI 没有进入请求"
        process.send_signal(getattr(signal, signal_name))
        stdout, stderr = process.communicate(timeout=3)
        assert process.returncode == expected
        assert b"fixture-signal-token" not in stdout + stderr
    finally:
        release.set()
        if process.poll() is None:
            process.kill()
            process.communicate()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_retry_after_respects_total_deadline() -> None:
    with endpoint([503]) as (url, requests):
        client = ApiClient(url, "temporary-fixture-token", timeout=0.05)
        # 即使服务端要求重试，也不能越过本次命令的总期限。
        client._sleep = lambda retry_after, attempt: ApiClient._sleep(client, "30", attempt)
        with pytest.raises(ClientError) as error:
            client.request("GET", "/status")
        assert error.value.code == "CLIENT_DEADLINE" and error.value.exit_code == 4
        assert len(requests) == 1
