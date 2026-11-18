"""Shared test fixtures: a local HTTP server with a route table.

No test touches the live internet. Everything runs against 127.0.0.1.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

import pytest

from pagemint.client import PageMint
from pagemint.config import Settings


@dataclass
class Response:
    body: bytes | str = b""
    status: int = 200
    headers: dict[str, str] = field(default_factory=lambda: {"Content-Type": "text/html; charset=utf-8"})
    delay: float = 0.0
    chunked: bool = False


Handler = Callable[["RequestInfo"], Response]


@dataclass
class RequestInfo:
    path: str
    headers: dict[str, str]
    time: float


class LocalServer:
    """A tiny threaded HTTP server. Register routes with ``server.route(path, Response)``."""

    def __init__(self) -> None:
        self.routes: dict[str, Response | Handler] = {}
        self.requests: list[RequestInfo] = []
        outer = self

        class _H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # silence
                pass

            def do_GET(self):  # noqa: N802
                info = RequestInfo(self.path, {k.lower(): v for k, v in self.headers.items()}, time.monotonic())
                outer.requests.append(info)
                route = outer.routes.get(self.path)
                if route is None:
                    route = outer.routes.get(self.path.split("?", 1)[0])
                if route is None:
                    resp = Response(b"not found", status=404, headers={"Content-Type": "text/plain"})
                elif callable(route):
                    resp = route(info)
                else:
                    resp = route
                if resp.delay:
                    time.sleep(resp.delay)
                body = resp.body.encode("utf-8") if isinstance(resp.body, str) else resp.body
                try:
                    self.send_response(resp.status)
                    for k, v in resp.headers.items():
                        self.send_header(k, v)
                    if resp.chunked:
                        self.send_header("Transfer-Encoding", "chunked")
                        self.end_headers()
                        step = 64 * 1024
                        for i in range(0, len(body), step):
                            part = body[i : i + step]
                            self.wfile.write(f"{len(part):x}\r\n".encode() + part + b"\r\n")
                        self.wfile.write(b"0\r\n\r\n")
                    else:
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def url(self, path: str = "/", host: str = "127.0.0.1") -> str:
        return f"http://{host}:{self.port}{path}"

    def route(self, path: str, resp: Response | Handler | str) -> None:
        if isinstance(resp, str):
            resp = Response(resp)
        self.routes[path] = resp

    def hits(self, path: str) -> int:
        return sum(1 for r in self.requests if r.path == path)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    s = LocalServer()
    # Default: an allow-all robots.txt so tests don't depend on 404 handling.
    s.route("/robots.txt", Response("User-agent: *\nAllow: /\n", headers={"Content-Type": "text/plain"}))
    s.start()
    yield s
    s.stop()


def make_settings(**overrides) -> Settings:
    base = dict(allow_private_networks=True, rate_limit=0.0)
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
async def client():
    c = PageMint(make_settings())
    yield c
    await c.aclose()
