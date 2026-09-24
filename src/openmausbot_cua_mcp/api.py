"""Client helpers for OpenMausBot's local administration API."""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
from datetime import date
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

DEFAULT_API_TIMEOUT_SECONDS = 10.0
DEFAULT_BASE_URL = "http://127.0.0.1:8799"
DISCOVERY_PORTS = (8799, 18799, 28799)
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
TESTED_APP_VERSIONS = ("0.1.85", "0.1.86")


class OpenMausBotApiError(RuntimeError):
    """Raised when the OpenMausBot administration API cannot satisfy a request."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def validate_base_url(url: str) -> str:
    """Validate and normalize an API origin."""
    if url != url.strip() or "?" in url or "#" in url:
        raise OpenMausBotApiError(
            "OpenMausBot API URL must be an origin without a path, query, or fragment."
        )
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise OpenMausBotApiError(f"Invalid OpenMausBot API URL: {exc}") from exc

    if parsed.scheme not in {"http", "https"}:
        raise OpenMausBotApiError("OpenMausBot API URL must use http or https.")
    if not parsed.netloc or parsed.hostname is None:
        raise OpenMausBotApiError("OpenMausBot API URL must include a host.")
    if parsed.username is not None or parsed.password is not None:
        raise OpenMausBotApiError("OpenMausBot API URL must not contain credentials.")
    if parsed.path or parsed.query or parsed.fragment:
        raise OpenMausBotApiError(
            "OpenMausBot API URL must be an origin without a path, query, or fragment."
        )
    host = parsed.hostname.lower()
    if parsed.scheme == "http" and host not in {"127.0.0.1", "localhost", "::1"}:
        raise OpenMausBotApiError("Plain HTTP is only allowed for 127.0.0.1, localhost, or ::1.")

    display_host = f"[{host}]" if ":" in host else host
    netloc = f"{display_host}:{port}" if port is not None else display_host
    return urlunsplit((parsed.scheme, netloc, "", "", ""))


def _timeout_from_environment() -> float:
    raw_timeout = os.environ.get("OPENMAUSBOT_API_TIMEOUT")
    if raw_timeout is None:
        return DEFAULT_API_TIMEOUT_SECONDS
    try:
        timeout = float(raw_timeout)
    except ValueError as exc:
        raise OpenMausBotApiError("OPENMAUSBOT_API_TIMEOUT must be a positive number.") from exc
    if timeout <= 0:
        raise OpenMausBotApiError("OPENMAUSBOT_API_TIMEOUT must be a positive number.")
    return timeout


def _read_response(response: Any) -> Any:
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_RESPONSE_BYTES:
                raise OpenMausBotApiError("OpenMausBot API response exceeded the 5 MB limit.")
        except ValueError:
            pass
    payload = response.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise OpenMausBotApiError("OpenMausBot API response exceeded the 5 MB limit.")
    try:
        return json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OpenMausBotApiError("OpenMausBot API returned a non-JSON response.") from exc


def discover_base_url(timeout: float) -> str:
    """Find the local API without sending authentication credentials."""
    configured_url = os.environ.get("OPENMAUSBOT_URL")
    if configured_url:
        return validate_base_url(configured_url)

    configured_port = os.environ.get("OMB_PORT")
    if configured_port:
        try:
            port = int(configured_port)
        except ValueError as exc:
            raise OpenMausBotApiError("OMB_PORT must be an integer from 1 to 65535.") from exc
        if not 1 <= port <= 65535:
            raise OpenMausBotApiError("OMB_PORT must be an integer from 1 to 65535.")
        return f"http://127.0.0.1:{port}"

    for port in DISCOVERY_PORTS:
        origin = f"http://127.0.0.1:{port}"
        request = Request(f"{origin}/api/health", headers={"Accept": "application/json"})
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = _read_response(response)
        except (HTTPError, URLError, OSError, OpenMausBotApiError):
            continue
        if isinstance(payload, dict) and payload.get("app") == "openmausbot":
            return origin

    ports = ", ".join(str(port) for port in DISCOVERY_PORTS)
    raise OpenMausBotApiError(
        f"OpenMausBot is not running or its API is not reachable on loopback ports {ports}."
    )


def resolve_token() -> str | None:
    """Resolve a paired-device session token without exposing it."""
    token = os.environ.get("OPENMAUSBOT_TOKEN")
    if token:
        return token

    service = os.environ.get("OPENMAUSBOT_TOKEN_KEYCHAIN_SERVICE")
    if not service:
        return None
    command = ["security", "find-generic-password", "-s", service]
    account = os.environ.get("OPENMAUSBOT_TOKEN_KEYCHAIN_ACCOUNT")
    if account:
        command.extend(["-a", account])
    command.append("-w")
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.rstrip("\r\n") or None


def redact(obj: Any) -> Any:
    """Recursively redact secrets, tokens, and webhook delivery URLs."""
    if isinstance(obj, dict):
        redacted = {}
        for key, value in obj.items():
            lowered = str(key).lower()
            if (
                "secret" in lowered
                or "token" in lowered
                or "url" in lowered
                and isinstance(value, str)
                and "/hooks/" in value
            ):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact(value)
        return redacted
    if isinstance(obj, list):
        return [redact(item) for item in obj]
    return obj


def _server_error_detail(exc: HTTPError, limit: int = 300) -> str:
    """Return the server's own error text (bounded), or an empty string."""
    try:
        raw = exc.read(64 * 1024)
    except (OSError, AttributeError):
        return ""
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return ""
    error = payload.get("error") if isinstance(payload, dict) else None
    return error.strip()[:limit] if isinstance(error, str) else ""


def _pairing_message() -> str:
    return (
        "This request requires a paired-device session token. Pair through the OpenMausBot app, "
        "then set OPENMAUSBOT_TOKEN or OPENMAUSBOT_TOKEN_KEYCHAIN_SERVICE."
    )


class ApiClient:
    """Small JSON client for OpenMausBot's local administration API."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        token: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.timeout = _timeout_from_environment() if timeout is None else timeout
        if self.timeout <= 0:
            raise OpenMausBotApiError("API timeout must be a positive number.")
        explicit_origin = (
            base_url is not None
            or bool(os.environ.get("OPENMAUSBOT_URL"))
            or bool(os.environ.get("OMB_PORT"))
        )
        self.base_url = (
            validate_base_url(base_url) if base_url is not None else discover_base_url(self.timeout)
        )
        # A port found by probing could belong to another local process, so a token is only ever
        # sent to an origin the user named explicitly (same rule as OpenMausBot's own MCP client).
        self.discovered = not explicit_origin
        self.token = resolve_token() if token is None else token

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: Any = None,
        require_token: bool = False,
    ) -> Any:
        """Send one bounded JSON request to the configured origin."""
        if not path.startswith("/") or path.startswith("//"):
            raise OpenMausBotApiError("API request path must start with one slash.")
        if require_token and not self.token:
            raise OpenMausBotApiError(_pairing_message(), status=403)
        if require_token and self.discovered:
            raise OpenMausBotApiError(
                "Set OPENMAUSBOT_URL or OMB_PORT when using a session token so it is never sent "
                "to a port found by discovery.",
                status=403,
            )
        send_token = bool(self.token) and not self.discovered

        url = f"{self.base_url}{path}"
        if query:
            values = {key: value for key, value in query.items() if value is not None}
            if values:
                url = f"{url}?{urlencode(values)}"
        headers = {"Accept": "application/json"}
        if send_token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method.upper())

        try:
            with urlopen(request, timeout=self.timeout) as response:
                return _read_response(response)
        except HTTPError as exc:
            detail = _server_error_detail(exc)
            if exc.code == 401:
                raise OpenMausBotApiError(
                    "OpenMausBot session expired or revoked; pair again.", status=401
                ) from exc
            if exc.code == 403 and not send_token:
                message = _pairing_message()
                if detail:
                    message = f"{message} Server said: {detail}"
                raise OpenMausBotApiError(message, status=403) from exc
            message = f"OpenMausBot API request failed with HTTP {exc.code}."
            if detail:
                message = f"{message} Server said: {detail}"
            raise OpenMausBotApiError(message, status=exc.code) from exc
        except (URLError, OSError) as exc:
            raise OpenMausBotApiError(
                f"OpenMausBot is not running or its API is not reachable at {self.base_url}"
            ) from exc

    def health(self) -> dict[str, Any]:
        return self.request("GET", "/api/health")

    def edition(self) -> dict[str, Any]:
        return self.request("GET", "/api/edition")

    def app_version(self) -> str | None:
        override = os.environ.get("OPENMAUSBOT_APP_PLIST")
        paths = (
            [Path(override).expanduser()]
            if override
            else [
                Path.home() / "Applications/OpenMausBot.app/Contents/Info.plist",
                Path("/Applications/OpenMausBot.app/Contents/Info.plist"),
            ]
        )
        for path in paths:
            try:
                with path.open("rb") as handle:
                    payload = plistlib.load(handle)
            except (FileNotFoundError, OSError, plistlib.InvalidFileException):
                continue
            if not isinstance(payload, dict):
                continue
            version = payload.get("CFBundleShortVersionString")
            if isinstance(version, str):
                return version
        return None

    def list_bots(self, include_soul: bool = False) -> dict[str, Any]:
        payload = self.request("GET", "/api/bots", query={"messages": 0})
        if include_soul or not isinstance(payload, dict):
            return payload
        bots = payload.get("bots")
        if not isinstance(bots, list):
            return payload
        return {
            **payload,
            "bots": [
                {key: value for key, value in bot.items() if key != "soul"}
                if isinstance(bot, dict)
                else bot
                for bot in bots
            ],
        }

    def list_routines(self, from_ms: int | None = None, to_ms: int | None = None) -> dict[str, Any]:
        return self.request("GET", "/api/routines", query={"from": from_ms, "to": to_ms})

    def patch_bot(self, bot_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        return self.request(
            "PATCH",
            f"/api/bots/{quote(bot_id, safe='')}",
            body=patch,
            require_token=True,
        )

    def create_routine(self, spec: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/api/routines", body=spec, require_token=True)

    def update_routine(self, routine_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        return self.request(
            "PATCH",
            f"/api/routines/{quote(routine_id, safe='')}",
            body=patch,
            require_token=True,
        )

    def delete_routine(self, routine_id: str) -> dict[str, Any]:
        return self.request(
            "DELETE",
            f"/api/routines/{quote(routine_id, safe='')}",
            require_token=True,
        )

    def run_routine(self, routine_id: str) -> dict[str, Any]:
        return self.request(
            "POST",
            f"/api/routines/{quote(routine_id, safe='')}/run",
            body={},
            require_token=True,
        )

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        return self.request(
            "POST",
            f"/api/routine-runs/{quote(run_id, safe='')}/cancel",
            body={},
            require_token=True,
        )

    def validate_model_selection(
        self,
        instance_id: str,
        model: str,
        effort: str | None = None,
    ) -> dict[str, str]:
        """Validate a selection against the server's current provider catalog."""
        payload = self.request("GET", "/api/instances")
        instances = payload.get("instances") if isinstance(payload, dict) else None
        if not isinstance(instances, list):
            raise OpenMausBotApiError("OpenMausBot returned an invalid provider instance list.")
        instance = next(
            (
                item
                for item in instances
                if isinstance(item, dict) and item.get("instanceId") == instance_id
            ),
            None,
        )
        if instance is None:
            raise OpenMausBotApiError(f"Unknown model instance: {instance_id}.")
        snapshot = instance.get("snapshot")
        state = snapshot.get("state") if isinstance(snapshot, dict) else None
        if state != "available":
            raise OpenMausBotApiError(
                f"Model instance {instance_id} is not available (state: {state or 'unknown'})."
            )
        models = instance.get("models")
        default = models.get("default") if isinstance(models, dict) else None
        options = models.get("options") if isinstance(models, dict) else None
        offered = {default} if isinstance(default, str) else set()
        if isinstance(options, list):
            offered.update(
                option.get("id")
                for option in options
                if isinstance(option, dict) and isinstance(option.get("id"), str)
            )
        if model not in offered:
            raise OpenMausBotApiError(
                f"Model {model} is not offered by instance {instance_id}."
            )
        capabilities = instance.get("capabilities")
        levels = capabilities.get("effortLevels") if isinstance(capabilities, dict) else None
        if effort is not None and (not isinstance(levels, list) or effort not in levels):
            raise OpenMausBotApiError(
                f"Effort {effort} is not supported by instance {instance_id}."
            )
        selection = {"instanceId": instance_id, "model": model}
        if effort is not None:
            selection["effort"] = effort
        return selection

    def list_webhooks(self) -> dict[str, Any]:
        return redact(self.request("GET", "/api/webhooks"))

    def usage(self, from_date: str, to_date: str, group_by: str | None = None) -> dict[str, Any]:
        try:
            start = date.fromisoformat(from_date)
            end = date.fromisoformat(to_date)
        except ValueError as exc:
            raise OpenMausBotApiError("Usage dates must use YYYY-MM-DD format.") from exc
        if start.isoformat() != from_date or end.isoformat() != to_date:
            raise OpenMausBotApiError("Usage dates must use YYYY-MM-DD format.")
        if end < start:
            raise OpenMausBotApiError("Usage to_date must not be earlier than from_date.")
        try:
            anniversary = start.replace(year=start.year + 1)
        except ValueError:
            anniversary = start.replace(year=start.year + 1, day=28)
        if end > anniversary:
            raise OpenMausBotApiError("Usage date range must not exceed one year.")
        return self.request(
            "GET",
            "/api/usage",
            query={"from": from_date, "to": to_date, "groupBy": group_by},
        )

    def list_models(self) -> dict[str, Any]:
        payload = self.request("GET", "/api/instances")
        if not isinstance(payload, dict) or not isinstance(payload.get("instances"), list):
            return payload
        instances = []
        for instance in payload["instances"]:
            if not isinstance(instance, dict):
                continue
            snapshot = instance.get("snapshot")
            capabilities = instance.get("capabilities")
            instances.append(
                {
                    "id": instance.get("instanceId"),
                    "driver": instance.get("driverKind"),
                    "displayName": instance.get("displayName"),
                    "state": snapshot.get("state") if isinstance(snapshot, dict) else None,
                    "models": instance.get("models"),
                    "effortLevels": capabilities.get("effortLevels")
                    if isinstance(capabilities, dict)
                    else None,
                }
            )
        return {"instances": instances}

    def decisions(self, limit: int = 50) -> dict[str, Any]:
        if not 1 <= limit <= 500:
            raise OpenMausBotApiError("Decision limit must be from 1 to 500.")
        return self.request("GET", "/api/decisions", query={"limit": limit})

    def export_team(self, format: str = "manifest") -> dict[str, Any]:
        if format not in {"manifest", "package", "backup"}:
            raise OpenMausBotApiError("Export format must be manifest, package, or backup.")
        return redact(
            self.request(
                "POST",
                "/api/teams/export",
                body={"format": format},
                require_token=True,
            )
        )

    def check_compatibility(self) -> dict[str, Any]:
        health = self.health()
        app = health.get("app") if isinstance(health, dict) else None
        version = self.app_version()
        warnings = []
        if app != "openmausbot":
            warnings.append("The health endpoint did not identify itself as OpenMausBot.")
        if version not in TESTED_APP_VERSIONS:
            displayed = version or "unknown"
            warnings.append(f"OpenMausBot {displayed} has not been tested with this package.")
        return {
            "ok": app == "openmausbot",
            "app": app,
            "version": version,
            "tested_versions": list(TESTED_APP_VERSIONS),
            "warnings": warnings,
        }
