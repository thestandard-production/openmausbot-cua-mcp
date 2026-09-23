from __future__ import annotations

import json
import sys
from pathlib import Path

from openmausbot_cua_mcp.bridge import (
    read_companion_settings,
    resolve_connection,
    run_cua,
    write_companion_setting,
)


def _write_connection(path: Path, driver: Path, socket_path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "socketPath": str(socket_path),
                "mcpCommand": str(driver),
                "mcpEnv": {
                    "CUA_DRIVER_EMBEDDED": "1",
                    "IGNORED_ENVIRONMENT_KEY": "not-forwarded",
                },
            }
        ),
        encoding="utf-8",
    )


def test_connection_is_resolved_fresh_for_every_call(tmp_path: Path, monkeypatch) -> None:
    connection = tmp_path / "cua-connection.json"
    first_socket = tmp_path / "first.sock"
    second_socket = tmp_path / "second.sock"
    first_socket.touch()
    second_socket.touch()
    _write_connection(connection, Path(sys.executable), first_socket)
    monkeypatch.setenv("OPENMAUSBOT_CUA_CONNECTION_FILE", str(connection))

    assert resolve_connection().socket_path == first_socket
    _write_connection(connection, Path(sys.executable), second_socket)
    assert resolve_connection().socket_path == second_socket


def test_run_cua_passes_socket_and_json_without_shell(tmp_path: Path, monkeypatch) -> None:
    driver = tmp_path / "fake-driver"
    driver.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "print(json.dumps({'argv': sys.argv[1:]}))\n",
        encoding="utf-8",
    )
    driver.chmod(0o755)
    socket_path = tmp_path / "driver.sock"
    socket_path.touch()
    connection = tmp_path / "cua-connection.json"
    _write_connection(connection, driver, socket_path)
    monkeypatch.setenv("OPENMAUSBOT_CUA_CONNECTION_FILE", str(connection))

    result = run_cua(["call", "get_screen_size", "{}"])
    payload = json.loads(result.stdout)

    assert result.ok is True
    assert payload["argv"] == [
        "--socket",
        str(socket_path),
        "call",
        "get_screen_size",
        "{}",
    ]


def test_companion_setting_write_is_guarded_and_atomic(tmp_path: Path, monkeypatch) -> None:
    settings_path = tmp_path / "companion-settings.json"
    settings_path.write_text('{"enabled": true, "keepAwake": false}\n', encoding="utf-8")
    monkeypatch.setenv("OPENMAUSBOT_COMPANION_SETTINGS_FILE", str(settings_path))

    rejected = write_companion_setting("typo", True)
    assert rejected["ok"] is False
    assert read_companion_settings()["keepAwake"] is False

    accepted = write_companion_setting("keepAwake", True)
    assert accepted["ok"] is True
    assert read_companion_settings()["keepAwake"] is True
    assert not list(tmp_path.glob(".companion-settings.json.tmp-*"))


def test_connection_environment_only_forwards_cua_driver_keys(tmp_path: Path, monkeypatch) -> None:
    connection = tmp_path / "cua-connection.json"
    socket_path = tmp_path / "driver.sock"
    socket_path.touch()
    _write_connection(connection, Path(sys.executable), socket_path)
    monkeypatch.setenv("OPENMAUSBOT_CUA_CONNECTION_FILE", str(connection))

    resolved = resolve_connection()
    assert resolved.environment == {"CUA_DRIVER_EMBEDDED": "1"}
    assert "IGNORED_ENVIRONMENT_KEY" not in resolved.environment
