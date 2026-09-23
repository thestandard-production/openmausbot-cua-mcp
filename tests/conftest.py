from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

import pytest


@pytest.fixture
def fake_api(monkeypatch):
    state: dict[str, Any] = {
        "responses": {
            ("GET", "/api/health"): (200, {"app": "openmausbot", "pid": 1}),
        },
        "requests": [],
        "expected_token": "paired-session",
    }

    class Handler(BaseHTTPRequestHandler):
        def _serve(self) -> None:
            parsed = urlsplit(self.path)
            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length) if length else b""
            try:
                request_body = json.loads(raw_body) if raw_body else None
            except json.JSONDecodeError:
                request_body = None
            request = {
                "method": self.command,
                "path": parsed.path,
                "query": parsed.query,
                "headers": dict(self.headers),
                "body": request_body,
            }
            state["requests"].append(request)
            if self.command != "GET" and self.headers.get("Authorization") != (
                f"Bearer {state['expected_token']}"
            ):
                status, payload = 403, {"error": "paired session required"}
            else:
                response = state["responses"].get(
                    (self.command, parsed.path), (404, {"error": "not found"})
                )
                status, payload = response(request) if callable(response) else response
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_DELETE = _serve
        do_GET = _serve
        do_PATCH = _serve
        do_POST = _serve

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("OPENMAUSBOT_URL", origin)
    monkeypatch.delenv("OMB_PORT", raising=False)
    monkeypatch.delenv("OPENMAUSBOT_TOKEN", raising=False)
    monkeypatch.delenv("OPENMAUSBOT_TOKEN_KEYCHAIN_SERVICE", raising=False)
    monkeypatch.delenv("OPENMAUSBOT_TOKEN_KEYCHAIN_ACCOUNT", raising=False)
    try:
        yield state, origin
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
