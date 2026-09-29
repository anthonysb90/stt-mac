"""A tiny local HTTP server standing in for the cloud services.

Faking at the socket rather than monkeypatching urllib means the real request
code runs: the multipart body, the headers, the query string, the timeouts and
the error handling are all exercised, and a test can read back exactly what was
sent.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, List, Tuple, Union

Reply = Tuple[int, Union[bytes, dict, list]]


class Recorded:
    def __init__(self, method: str, path: str, headers: dict, body: bytes) -> None:
        self.method = method
        self.path = path
        #: Lower-cased: urllib re-capitalises names ("Xi-api-key"), and HTTP
        #: header names are case-insensitive anyway.
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.body = body

    def json(self):
        return json.loads(self.body.decode("utf-8"))


class FakeServer:
    """Routes ``(METHOD, path-prefix)`` to a reply or a function returning one."""

    def __init__(self) -> None:
        self.routes: Dict[Tuple[str, str], Union[Reply, Callable[[Recorded], Reply]]] = {}
        self.requests: List[Recorded] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):  # keep test output clean
                pass

            def _handle(self, method: str) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                recorded = Recorded(method, self.path, dict(self.headers), body)
                server.requests.append(recorded)
                reply = server._match(method, self.path)
                if callable(reply):
                    reply = reply(recorded)
                status, payload = reply
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def _match(self, method: str, path: str):
        best = None
        for (m, prefix), reply in self.routes.items():
            if m == method and path.startswith(prefix):
                if best is None or len(prefix) > len(best[0]):
                    best = (prefix, reply)
        return best[1] if best else (404, {"error": "no route"})

    def start(self) -> "FakeServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
