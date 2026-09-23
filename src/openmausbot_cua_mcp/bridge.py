"""Discovery and subprocess helpers for OpenMausBot's bundled cua-driver."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_BUNDLE_ID = "com.openmausbot.app"
DEFAULT_TIMEOUT_SECONDS = 15
DEFAULT_MAX_OUTPUT_CHARS = 200_000


class OpenMausBotConnectionError(RuntimeError):
    """Raised when a live OpenMausBot cua-driver connection cannot be resolved."""


@dataclass(frozen=True)
class ConnectionInfo:
    command: Path
    socket_path: Path
    bundle_id: str
    environment: dict[str, str]
    connection_file: Path


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    returncode: int
    stdout: str
    stderr: str
    socket_path: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "socket_path": self.socket_path,
        }


def data_directory() -> Path:
    override = os.environ.get("OPENMAUSBOT_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library" / "Application Support" / "openmausbot"


def connection_file() -> Path:
    override = os.environ.get("OPENMAUSBOT_CUA_CONNECTION_FILE")
    if override:
        return Path(override).expanduser()
    return data_directory() / "cua-connection.json"


def companion_settings_file() -> Path:
    override = os.environ.get("OPENMAUSBOT_COMPANION_SETTINGS_FILE")
    if override:
        return Path(override).expanduser()
    return data_directory() / "companion-settings.json"


def _candidate_driver_paths() -> list[Path]:
    home = Path.home()
    return [
        home / "Applications/OpenMausBot.app/Contents/Resources/cua-driver",
        Path("/Applications/OpenMausBot.app/Contents/Resources/cua-driver"),
    ]


def _read_connection_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise OpenMausBotConnectionError(
            f"OpenMausBot connection file was not found at {path}. Start OpenMausBot and retry."
        ) from exc
    except json.JSONDecodeError as exc:
        raise OpenMausBotConnectionError(
            f"OpenMausBot connection file is invalid JSON: {path}. Restart OpenMausBot and retry."
        ) from exc
    if not isinstance(payload, dict):
        raise OpenMausBotConnectionError(
            f"OpenMausBot connection file must contain a JSON object: {path}."
        )
    return payload


def resolve_connection() -> ConnectionInfo:
    """Resolve the live command and socket every time OpenMausBot is called."""
    path = connection_file()
    payload = _read_connection_payload(path)

    command_value = os.environ.get("OPENMAUSBOT_CUA_DRIVER")
    if not command_value and isinstance(payload.get("mcpCommand"), str):
        command_value = payload["mcpCommand"]
    if command_value:
        command = Path(command_value).expanduser()
    else:
        command = next(
            (candidate for candidate in _candidate_driver_paths() if candidate.is_file()), None
        )
        if command is None:
            raise OpenMausBotConnectionError(
                "cua-driver was not found. Install OpenMausBot or set OPENMAUSBOT_CUA_DRIVER."
            )
    if not command.is_file():
        raise OpenMausBotConnectionError(
            f"cua-driver was not found at {command}. Restart OpenMausBot or set "
            "OPENMAUSBOT_CUA_DRIVER."
        )

    socket_value = os.environ.get("OPENMAUSBOT_CUA_SOCKET")
    if not socket_value and isinstance(payload.get("socketPath"), str):
        socket_value = payload["socketPath"]
    if not socket_value:
        raise OpenMausBotConnectionError(
            f"No cua-driver socket is recorded in {path}. Start OpenMausBot and retry."
        )
    socket_path = Path(socket_value).expanduser()
    if not socket_path.exists():
        raise OpenMausBotConnectionError(
            f"The recorded cua-driver socket does not exist: {socket_path}. "
            "Restart OpenMausBot and retry."
        )

    raw_environment = payload.get("mcpEnv")
    environment = {}
    if isinstance(raw_environment, dict):
        environment = {
            key: str(value)
            for key, value in raw_environment.items()
            if isinstance(key, str)
            and key.startswith("CUA_DRIVER_")
            and isinstance(value, (str, int, float, bool))
        }
    bundle_id = os.environ.get(
        "OPENMAUSBOT_BUNDLE_ID",
        environment.get("CUA_DRIVER_HOST_BUNDLE_ID", DEFAULT_BUNDLE_ID),
    )
    return ConnectionInfo(
        command=command,
        socket_path=socket_path,
        bundle_id=bundle_id,
        environment=environment,
        connection_file=path,
    )


def run_cua(arguments: list[str], timeout: int = DEFAULT_TIMEOUT_SECONDS) -> CommandResult:
    """Run one cua-driver CLI command against OpenMausBot's current embedded daemon."""
    connection = resolve_connection()
    environment = os.environ.copy()
    environment.update(connection.environment)
    environment.update(
        {
            "CUA_DRIVER_SOCKET": str(connection.socket_path),
            "CUA_DRIVER_HOST_BUNDLE_ID": connection.bundle_id,
            "CUA_DRIVER_EMBEDDED": "1",
            "CUA_DRIVER_RS_TELEMETRY_ENABLED": "0",
        }
    )
    max_output = int(os.environ.get("OPENMAUSBOT_CUA_MAX_OUTPUT_CHARS", DEFAULT_MAX_OUTPUT_CHARS))
    try:
        result = subprocess.run(
            [str(connection.command), "--socket", str(connection.socket_path), *arguments],
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return CommandResult(
            ok=result.returncode == 0,
            returncode=result.returncode,
            stdout=result.stdout[:max_output],
            stderr=result.stderr[:max_output],
            socket_path=str(connection.socket_path),
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            ok=False,
            returncode=-1,
            stdout=(exc.stdout or "")[:max_output] if isinstance(exc.stdout, str) else "",
            stderr=f"cua-driver timed out after {timeout} seconds",
            socket_path=str(connection.socket_path),
        )
    except OSError as exc:
        return CommandResult(
            ok=False,
            returncode=-1,
            stdout="",
            stderr=f"cua-driver could not be started: {exc}",
            socket_path=str(connection.socket_path),
        )


def read_companion_settings() -> dict[str, Any]:
    path = companion_settings_file()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise OpenMausBotConnectionError(
            f"OpenMausBot companion settings were not found at {path}."
        ) from exc
    except json.JSONDecodeError as exc:
        raise OpenMausBotConnectionError(
            f"OpenMausBot companion settings are invalid JSON: {path}."
        ) from exc
    if not isinstance(payload, dict):
        raise OpenMausBotConnectionError(f"Companion settings must contain a JSON object: {path}.")
    return payload


def write_companion_setting(key: str, value: Any) -> dict[str, Any]:
    path = companion_settings_file()
    settings = read_companion_settings()
    if key not in settings:
        return {
            "ok": False,
            "error": f"Unknown companion setting: {key}",
            "allowed_keys": sorted(settings),
        }
    settings[key] = value
    try:
        serialized = json.dumps(settings, ensure_ascii=False, indent=2) + "\n"
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": f"Setting value is not JSON serializable: {exc}"}

    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        mode = path.stat().st_mode
        temporary.write_text(serialized, encoding="utf-8")
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return {"ok": True, "key": key, "value": value, "settings": settings}
