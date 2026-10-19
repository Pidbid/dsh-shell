from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from dsh_shell.dsh_runtime import (
    DshRuntime,
    build_token_url,
    mask_url,
    port_from_url,
    probe_url,
)


class Handler(BaseHTTPRequestHandler):
    token = "test-token-12345678"

    def do_GET(self) -> None:  # noqa: N802
        split = urlsplit(self.path)
        if split.path == "/" and parse_qs(split.query).get("token") == [self.token]:
            self.send_response(303)
            self.send_header("Location", "./")
            self.end_headers()
            return
        if split.path == "/":
            body = b"DeepSeek Harness"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture()
def server() -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def test_probe_url_status(server: ThreadingHTTPServer) -> None:
    port = server.server_address[1]
    assert probe_url(f"http://127.0.0.1:{port}/")[0] == 200
    assert probe_url(f"http://127.0.0.1:{port}/missing")[0] == 404
    status, location = probe_url(build_token_url(port, Handler.token))
    assert status == 303
    assert location == "./"


def test_explicit_url_reuses_authenticated_endpoint(server: ThreadingHTTPServer) -> None:
    port = server.server_address[1]
    runtime = DshRuntime(
        explicit_url=build_token_url(port, Handler.token),
        reuse=False,
        spawn=False,
        preferred_port=port,
    )
    endpoint = runtime.ensure()
    assert endpoint.healthy is True
    assert endpoint.port == port
    assert endpoint.token == Handler.token
    assert endpoint.source == "explicit"


def test_explicit_unreachable_url_does_not_spawn() -> None:
    runtime = DshRuntime(
        explicit_url="http://127.0.0.1:1/",
        reuse=False,
        spawn=False,
    )
    with pytest.raises(Exception, match="无法连接"):
        runtime.ensure()


def test_url_helpers() -> None:
    assert port_from_url("http://127.0.0.1:3080/") == 3080
    assert "***" in mask_url("http://127.0.0.1:3080/?token=abcdefgh")
    assert mask_url("http://127.0.0.1:3080/") == "http://127.0.0.1:3080/"
    assert build_token_url(3080, "a_b-c") == "http://127.0.0.1:3080/?token=a_b-c"
