from __future__ import annotations

import json
import plistlib
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from openmausbot_cua_mcp import api
from openmausbot_cua_mcp.api import (
    ApiClient,
    OpenMausBotApiError,
    resolve_token,
    validate_base_url,
)
from openmausbot_cua_mcp.cli import main


@pytest.fixture
def fake_api(monkeypatch):
    state: dict[str, Any] = {
        "responses": {
            ("GET", "/api/health"): (200, {"app": "openmausbot", "pid": 1}),
        },
        "requests": [],
    }

    class Handler(BaseHTTPRequestHandler):
        def _serve(self) -> None:
            parsed = urlsplit(self.path)
            state["requests"].append(
                {
                    "method": self.command,
                    "path": parsed.path,
                    "query": parsed.query,
                    "headers": dict(self.headers),
                }
            )
            status, payload = state["responses"].get(
                (self.command, parsed.path), (404, {"error": "not found"})
            )
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = _serve
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


def test_discovery_does_not_send_token(fake_api, monkeypatch) -> None:
    state, origin = fake_api
    monkeypatch.delenv("OPENMAUSBOT_URL")
    monkeypatch.setenv("OPENMAUSBOT_TOKEN", "paired-session")
    monkeypatch.setattr(api, "DISCOVERY_PORTS", (urlsplit(origin).port,))

    client = ApiClient()
    client.health()

    health_requests = [request for request in state["requests"] if request["path"] == "/api/health"]
    assert len(health_requests) == 2
    # Neither the discovery probe nor later reads to a discovered port carry the token.
    for request in health_requests:
        assert "Authorization" not in request["headers"]
        assert "Origin" not in request["headers"]
    assert client.discovered is True


def test_discovered_origin_refuses_token_writes(fake_api, monkeypatch) -> None:
    state, origin = fake_api
    monkeypatch.delenv("OPENMAUSBOT_URL")
    monkeypatch.setenv("OPENMAUSBOT_TOKEN", "paired-session")
    monkeypatch.setattr(api, "DISCOVERY_PORTS", (urlsplit(origin).port,))
    state["responses"][("POST", "/api/teams/export")] = (200, {"ok": True})

    client = ApiClient()
    with pytest.raises(OpenMausBotApiError, match="OPENMAUSBOT_URL or OMB_PORT"):
        client.export_team()

    assert not [request for request in state["requests"] if request["method"] == "POST"]


def test_explicit_origin_sends_token(fake_api, monkeypatch) -> None:
    state, origin = fake_api
    monkeypatch.setenv("OPENMAUSBOT_TOKEN", "paired-session")
    state["responses"][("POST", "/api/teams/export")] = (200, {"manifest": {"bots": []}})

    ApiClient().export_team()

    post = next(request for request in state["requests"] if request["method"] == "POST")
    assert post["headers"]["Authorization"] == "Bearer paired-session"


def test_server_error_text_is_included(fake_api) -> None:
    state, origin = fake_api
    state["responses"][("POST", "/busy")] = (409, {"error": "A bot is working right now"})

    with pytest.raises(OpenMausBotApiError, match="A bot is working right now") as caught:
        ApiClient(origin, token="paired-session").request(
            "POST", "/busy", body={}, require_token=True
        )

    assert caught.value.status == 409


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test:8799",
        "http://127.0.0.1:8799/api",
        "http://127.0.0.1:8799?query=yes",
        "http://127.0.0.1:8799?",
        "http://127.0.0.1:8799#fragment",
        "http://127.0.0.1:8799#",
        "http://user:password@127.0.0.1:8799",
    ],
)
def test_validate_base_url_rejects_unsafe_origins(url: str) -> None:
    with pytest.raises(OpenMausBotApiError):
        validate_base_url(url)


def test_validate_base_url_accepts_loopback_http_and_remote_https() -> None:
    assert validate_base_url("http://localhost:8799") == "http://localhost:8799"
    assert validate_base_url("http://[::1]:8799") == "http://[::1]:8799"
    assert validate_base_url("https://api.example.test") == "https://api.example.test"


def test_token_environment_precedes_keychain(monkeypatch) -> None:
    monkeypatch.setenv("OPENMAUSBOT_TOKEN", "environment-session")
    monkeypatch.setenv("OPENMAUSBOT_TOKEN_KEYCHAIN_SERVICE", "api-session")

    def fail(*args, **kwargs):
        raise AssertionError("Keychain must not be read when the token environment variable is set")

    monkeypatch.setattr(api.subprocess, "run", fail)
    assert resolve_token() == "environment-session"


def test_token_can_be_read_from_keychain(monkeypatch) -> None:
    monkeypatch.delenv("OPENMAUSBOT_TOKEN", raising=False)
    monkeypatch.setenv("OPENMAUSBOT_TOKEN_KEYCHAIN_SERVICE", "api-session")
    monkeypatch.setenv("OPENMAUSBOT_TOKEN_KEYCHAIN_ACCOUNT", "paired-device")
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        return subprocess.CompletedProcess(command, 0, "keychain-session\n", "")

    monkeypatch.setattr(api.subprocess, "run", fake_run)
    assert resolve_token() == "keychain-session"
    assert seen["command"] == [
        "security",
        "find-generic-password",
        "-s",
        "api-session",
        "-a",
        "paired-device",
        "-w",
    ]


def test_forbidden_without_token_explains_pairing(fake_api) -> None:
    state, origin = fake_api
    state["responses"][("POST", "/forbidden")] = (403, {"error": "forbidden"})

    with pytest.raises(OpenMausBotApiError) as caught:
        ApiClient(origin).request("POST", "/forbidden", body={})

    assert caught.value.status == 403
    assert "Pair through the OpenMausBot app" in str(caught.value)
    assert "OPENMAUSBOT_TOKEN" in str(caught.value)
    assert "OPENMAUSBOT_TOKEN_KEYCHAIN_SERVICE" in str(caught.value)


def test_unauthorized_explains_expired_session(fake_api) -> None:
    state, origin = fake_api
    state["responses"][("GET", "/expired")] = (401, {"error": "unauthorized"})

    with pytest.raises(OpenMausBotApiError, match="expired or revoked; pair again") as caught:
        ApiClient(origin).request("GET", "/expired")

    assert caught.value.status == 401


def test_connection_refused_names_origin() -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"

    with pytest.raises(OpenMausBotApiError) as caught:
        ApiClient(origin, timeout=0.2).health()

    assert str(caught.value) == (
        f"OpenMausBot is not running or its API is not reachable at {origin}"
    )


def test_webhook_output_is_recursively_redacted(fake_api) -> None:
    state, origin = fake_api
    state["responses"][("GET", "/api/webhooks")] = (
        200,
        {
            "webhooks": [
                {
                    "id": "one",
                    "secret": "hidden",
                    "deliveryToken": "hidden",
                    "url": "https://example.test/hooks/private-value",
                    "docsUrl": "https://example.test/help",
                }
            ],
            "attempts": [{"request": {"token": "hidden"}}],
            "ingress": {"available": True, "baseUrl": "https://example.test"},
        },
    )

    payload = ApiClient(origin).list_webhooks()

    webhook = payload["webhooks"][0]
    assert webhook["secret"] == "[REDACTED]"
    assert webhook["deliveryToken"] == "[REDACTED]"
    assert webhook["url"] == "[REDACTED]"
    assert webhook["docsUrl"] == "https://example.test/help"
    assert payload["attempts"][0]["request"]["token"] == "[REDACTED]"
    assert payload["ingress"]["baseUrl"] == "https://example.test"


def test_models_drop_snapshot_account_and_trim_instances(fake_api) -> None:
    state, origin = fake_api
    state["responses"][("GET", "/api/instances")] = (
        200,
        {
            "instances": [
                {
                    "instanceId": "local",
                    "driverKind": "example",
                    "displayName": "Local",
                    "snapshot": {"state": "ready", "account": "person@example.test"},
                    "models": {"default": "model-a", "options": []},
                    "capabilities": {"effortLevels": ["low", "high"]},
                    "unrelated": "removed",
                }
            ]
        },
    )

    instance = ApiClient(origin).list_models()["instances"][0]

    assert instance == {
        "id": "local",
        "driver": "example",
        "displayName": "Local",
        "state": "ready",
        "models": {"default": "model-a", "options": []},
        "effortLevels": ["low", "high"],
    }
    assert "account" not in json.dumps(instance)


def test_bots_omit_soul_by_default(fake_api) -> None:
    state, origin = fake_api
    state["responses"][("GET", "/api/bots")] = (
        200,
        {"bots": [{"id": "bot-1", "name": "Example", "soul": "long text"}]},
    )
    client = ApiClient(origin)

    assert "soul" not in client.list_bots()["bots"][0]
    assert client.list_bots(include_soul=True)["bots"][0]["soul"] == "long text"
    bot_requests = [request for request in state["requests"] if request["path"] == "/api/bots"]
    assert all(request["query"] == "messages=0" for request in bot_requests)


def test_compatibility_fails_closed_for_wrong_app(fake_api) -> None:
    state, origin = fake_api
    state["responses"][("GET", "/api/health")] = (200, {"app": "different-app"})

    compatibility = ApiClient(origin).check_compatibility()

    assert compatibility["ok"] is False
    assert compatibility["app"] == "different-app"
    assert compatibility["warnings"]


def _write_version_plist(path: Path, version: str) -> None:
    with path.open("wb") as handle:
        plistlib.dump({"CFBundleShortVersionString": version}, handle)


def test_cli_exit_codes(fake_api, monkeypatch, tmp_path: Path, capsys) -> None:
    state, _origin = fake_api
    plist = tmp_path / "Info.plist"
    monkeypatch.setenv("OPENMAUSBOT_APP_PLIST", str(plist))

    _write_version_plist(plist, "0.1.85")
    assert main(["status"]) == 0
    capsys.readouterr()

    _write_version_plist(plist, "9.9.9")
    assert main(["status"]) == 10
    capsys.readouterr()

    state["responses"][("GET", "/api/health")] = (200, {"app": "different-app"})
    assert main(["status"]) == 2
    capsys.readouterr()
