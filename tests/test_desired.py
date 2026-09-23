from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from openmausbot_cua_mcp import desired
from openmausbot_cua_mcp.api import ApiClient, OpenMausBotApiError
from openmausbot_cua_mcp.cli import main


def _desired_file(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _configure(state: dict[str, Any], bot: dict[str, Any]) -> None:
    state["responses"][("GET", "/api/bots")] = lambda _request: (200, {"bots": [bot]})
    state["responses"][("GET", "/api/routines")] = (
        200,
        {"routines": [], "runs": []},
    )


def test_desired_path_escape_is_rejected(fake_api, tmp_path: Path) -> None:
    state, origin = fake_api
    _configure(state, {"id": "bot-1", "name": "research-bot", "computer": "off"})
    outside = tmp_path / "outside.md"
    outside.write_text("private", encoding="utf-8")
    directory = tmp_path / "desired"
    directory.mkdir()
    path = _desired_file(
        directory / "desired.json",
        {
            "version": 1,
            "bots": [{"name": "research-bot", "soul_file": "../outside.md"}],
        },
    )

    result = desired.plan(ApiClient(origin), path)

    assert result["ok"] is False
    assert result["items"][0]["action"] == "error"
    assert "escapes" in result["items"][0]["error"]
    with pytest.raises(OpenMausBotApiError, match="escapes"):
        desired.apply(ApiClient(origin), path, only="bot:research-bot")


def test_plan_soul_diff_is_hashed_and_never_returned(fake_api, tmp_path: Path) -> None:
    state, origin = fake_api
    _configure(
        state,
        {"id": "bot-1", "name": "research-bot", "soul": "old secret", "computer": "off"},
    )
    (tmp_path / "soul.md").write_text("new secret", encoding="utf-8")
    path = _desired_file(
        tmp_path / "desired.json",
        {"version": 1, "bots": [{"name": "research-bot", "soul_file": "soul.md"}]},
    )

    result = desired.plan(ApiClient(origin), path)

    assert result["items"][0]["action"] == "update"
    soul_diff = result["items"][0]["diffs"]["soul"]
    assert soul_diff["before"]["bytes"] == len("old secret")
    assert soul_diff["after"]["bytes"] == len("new secret")
    assert "secret" not in json.dumps(result)


def test_plan_cli_exit_10_on_diff_and_0_on_none(fake_api, tmp_path: Path, capsys) -> None:
    state, _origin = fake_api
    bot = {"id": "bot-1", "name": "research-bot", "title": "Old", "computer": "off"}
    _configure(state, bot)
    path = _desired_file(
        tmp_path / "desired.json",
        {"version": 1, "bots": [{"name": "research-bot", "title": "New"}]},
    )

    assert main(["plan", str(path)]) == 10
    capsys.readouterr()
    bot["title"] = "New"
    assert main(["plan", str(path)]) == 0


def test_apply_only_bare_name_must_be_unambiguous(fake_api, tmp_path: Path) -> None:
    _state, origin = fake_api
    path = _desired_file(
        tmp_path / "desired.json",
        {
            "version": 1,
            "bots": [{"name": "same"}],
            "routines": [{"name": "same", "bot": "same", "prompt_file": "prompt.md"}],
        },
    )

    with pytest.raises(OpenMausBotApiError, match="ambiguous"):
        desired.apply(ApiClient(origin), path, only="same")


def test_soul_files_are_joined_in_declared_order(fake_api, tmp_path: Path) -> None:
    state, origin = fake_api
    _configure(state, {"id": "bot-1", "name": "research-bot", "soul": "", "computer": "off"})
    (tmp_path / "base.md").write_text("base", encoding="utf-8")
    (tmp_path / "common.md").write_text("common", encoding="utf-8")
    path = _desired_file(
        tmp_path / "desired.json",
        {
            "version": 1,
            "bots": [
                {
                    "name": "research-bot",
                    "soul_file": "base.md",
                    "soul_append_files": ["common.md"],
                }
            ],
        },
    )

    result = desired.apply(ApiClient(origin), path, only="bot:research-bot")

    expected = b"base\n\ncommon"
    assert result["request"]["body"]["soul"]["bytes"] == len(expected)
