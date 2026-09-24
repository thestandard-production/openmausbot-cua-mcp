from __future__ import annotations

import json
from typing import Any

import pytest

from openmausbot_cua_mcp import admin
from openmausbot_cua_mcp.api import ApiClient, OpenMausBotApiError
from openmausbot_cua_mcp.cli import main
from openmausbot_cua_mcp.guards import SOUL_MAX_BYTES, validate_bot_patch


def _configure_bot(state: dict[str, Any], bot: dict[str, Any]) -> None:
    state["responses"][("GET", "/api/bots")] = lambda _request: (200, {"bots": [bot]})


def _configure_models(state: dict[str, Any], *, state_name: str = "available") -> None:
    state["responses"][("GET", "/api/instances")] = (
        200,
        {
            "instances": [
                {
                    "instanceId": "provider-1",
                    "snapshot": {"state": state_name},
                    "models": {
                        "default": "model-a",
                        "options": [{"id": "model-b"}],
                    },
                    "capabilities": {"effortLevels": ["low", "high"]},
                }
            ]
        },
    )


def _configure_routines(
    state: dict[str, Any],
    routines: list[dict[str, Any]],
    runs: list[dict[str, Any]] | None = None,
) -> None:
    state["responses"][("GET", "/api/routines")] = lambda _request: (
        200,
        {"routines": routines, "runs": runs or []},
    )


def test_write_without_token_fails_before_mutation(fake_api) -> None:
    state, origin = fake_api

    with pytest.raises(OpenMausBotApiError, match="paired-device session token"):
        ApiClient(origin).patch_bot("bot-1", {"title": "New"})

    assert not [request for request in state["requests"] if request["method"] == "PATCH"]


def test_update_bot_dry_run_sends_no_mutation(fake_api) -> None:
    state, origin = fake_api
    bot = {"id": "bot-1", "name": "research-bot", "title": "Old", "computer": "off"}
    _configure_bot(state, bot)

    result = admin.update_bot(ApiClient(origin), "research-bot", {"title": "New"})

    assert result["action"] == "update"
    assert result["dry_run"] is True
    assert not [request for request in state["requests"] if request["method"] == "PATCH"]


def test_applied_patch_is_verified_and_soul_is_hashed(fake_api, monkeypatch) -> None:
    state, origin = fake_api
    monkeypatch.setenv("OPENMAUSBOT_TOKEN", "paired-session")
    bot = {
        "id": "bot-1",
        "name": "research-bot",
        "title": "Old",
        "soul": "old soul",
        "computer": "off",
    }
    _configure_bot(state, bot)

    def patch(request):
        bot.update(
            {
                key: value
                for key, value in request["body"].items()
                if key != "requireAvailableModel"
            }
        )
        return 200, {"bot": dict(bot)}

    state["responses"][("PATCH", "/api/bots/bot-1")] = patch

    result = admin.update_bot(
        ApiClient(origin), "bot-1", {"title": "New", "soul": "new soul"}, dry_run=False
    )

    assert result["ok"] is True
    assert result["verified"] is True
    assert result["after"]["title"] == "New"
    assert result["request"]["body"]["soul"]["bytes"] == 8
    assert "new soul" not in json.dumps(result)


def test_readback_mismatch_is_unknown_and_cli_exit_2(fake_api, monkeypatch) -> None:
    state, origin = fake_api
    monkeypatch.setenv("OPENMAUSBOT_TOKEN", "paired-session")
    bot = {"id": "bot-1", "name": "research-bot", "title": "Old", "computer": "off"}
    _configure_bot(state, bot)
    state["responses"][("PATCH", "/api/bots/bot-1")] = (200, {"bot": {**bot, "title": "New"}})

    result = admin.update_bot(ApiClient(origin), "bot-1", {"title": "New"}, dry_run=False)
    assert result["ok"] is False
    assert result["status"] == "unknown"
    assert result["mismatches"] == ["title"]

    assert main(["bot-update", "bot-1", "--json", '{"title":"New"}', "--apply"]) == 2


def test_bot_patch_allows_only_ask_approval_and_rejects_large_soul() -> None:
    assert validate_bot_patch({"approvalMode": "ask"}) == {"approvalMode": "ask"}
    for mode in ("auto", "full", "custom"):
        with pytest.raises(OpenMausBotApiError, match="only sets a bot's approvalMode to 'ask'"):
            validate_bot_patch({"approvalMode": mode})
    with pytest.raises(OpenMausBotApiError, match=str(SOUL_MAX_BYTES)):
        validate_bot_patch({"soul": "é" * (SOUL_MAX_BYTES // 2 + 1)})


def test_loosening_requires_confirmation_and_allowlist(fake_api, monkeypatch) -> None:
    state, origin = fake_api
    bot = {
        "id": "bot-1",
        "name": "research-bot",
        "computer": "off",
        "mcpServers": [],
    }
    _configure_bot(state, bot)
    client = ApiClient(origin)

    with pytest.raises(OpenMausBotApiError, match="loosens access"):
        admin.update_bot(client, "bot-1", {"computer": "browser"})
    allowed = admin.update_bot(
        client, "bot-1", {"computer": "browser"}, allow_loosen=True
    )
    assert allowed["action"] == "update"

    monkeypatch.setenv("OPENMAUSBOT_MCP_ALLOWLIST", "approved")
    with pytest.raises(OpenMausBotApiError, match="not permitted"):
        admin.update_bot(
            client,
            "bot-1",
            {"mcpServers": ["unapproved"]},
            allow_loosen=True,
        )


@pytest.mark.parametrize(
    ("instance", "model", "effort", "message"),
    [
        ("missing", "model-a", None, "Unknown model instance"),
        ("provider-1", "missing", None, "not offered"),
        ("provider-1", "model-a", "medium", "not supported"),
    ],
)
def test_model_validation_errors(fake_api, instance, model, effort, message) -> None:
    state, origin = fake_api
    _configure_models(state)

    with pytest.raises(OpenMausBotApiError, match=message):
        ApiClient(origin).validate_model_selection(instance, model, effort)


def test_model_validation_rejects_unavailable_and_accepts_option(fake_api) -> None:
    state, origin = fake_api
    _configure_models(state, state_name="offline")
    with pytest.raises(OpenMausBotApiError, match="not available"):
        ApiClient(origin).validate_model_selection("provider-1", "model-a")

    _configure_models(state)
    assert ApiClient(origin).validate_model_selection("provider-1", "model-b", "high") == {
        "instanceId": "provider-1",
        "model": "model-b",
        "effort": "high",
    }


def test_busy_bot_refuses_model_change(fake_api) -> None:
    state, origin = fake_api
    _configure_bot(
        state,
        {"id": "bot-1", "name": "research-bot", "busy": True, "computer": "off"},
    )

    with pytest.raises(OpenMausBotApiError, match="busy"):
        admin.set_bot_model(ApiClient(origin), "bot-1", "provider-1", "model-a")


def test_routine_upsert_create_update_noop_and_duplicate(fake_api) -> None:
    state, origin = fake_api
    bot = {"id": "bot-1", "name": "research-bot", "computer": "off"}
    _configure_bot(state, bot)
    routines: list[dict[str, Any]] = []
    _configure_routines(state, routines)
    client = ApiClient(origin)
    spec = {
        "name": "daily-summary",
        "prompt": "Summarize.\n",
        "bot": "research-bot",
        "schedule": {"type": "daily", "time": "09:00", "weekdays": ["friday", 1]},
    }

    assert admin.upsert_routine(client, spec)["action"] == "create"

    # What the server stores: trimmed prompt, canonical schedule.
    routines.append(
        {
            "id": "routine-1",
            "name": "daily-summary",
            "prompt": "Old prompt",
            "botId": "bot-1",
            "schedule": {"type": "daily", "time": "09:00", "weekdays": [1, 5]},
        }
    )
    assert admin.upsert_routine(client, spec)["action"] == "update"
    routines[0]["prompt"] = "Summarize."
    assert admin.upsert_routine(client, spec)["action"] == "noop"

    routines.append({**routines[0], "id": "routine-2"})
    with pytest.raises(OpenMausBotApiError, match="duplicate routine names"):
        admin.upsert_routine(client, spec)


def test_delete_requires_exact_confirm_name(fake_api) -> None:
    state, origin = fake_api
    _configure_routines(
        state,
        [{"id": "routine-1", "name": "daily-summary", "enabled": True}],
    )

    with pytest.raises(OpenMausBotApiError, match="exactly match"):
        admin.delete_routine(ApiClient(origin), "routine-1", "Daily Summary")


def test_normalize_schedule_matches_server_canonical_shapes() -> None:
    once = admin.normalize_schedule({"type": "once", "at": "2026-10-01T09:00:00+07:00"})
    assert once == {"type": "once", "at": 1790820000000}
    assert admin.normalize_schedule({"type": "once", "at": 1790820000000}) == once

    assert admin.normalize_schedule({"type": "daily", "time": "07:30"}) == {
        "type": "daily",
        "time": "07:30",
        "weekdays": [0, 1, 2, 3, 4, 5, 6],
    }
    assert admin.normalize_schedule(
        {"type": "daily", "time": "07:30", "weekdays": ["Monday", 1, 3]}
    )["weekdays"] == [1, 3]

    interval = admin.normalize_schedule(
        {
            "type": "interval",
            "everyMinutes": 60,
            "anchorAt": "2026-10-01T08:00:00+07:00",
            "weekdays": [0, 1, 2, 3, 4, 5, 6],
            "window": {"start": "08:00", "end": "20:00"},
        }
    )
    assert "weekdays" not in interval  # all seven days is stored as "no restriction"
    assert interval["anchorAt"] == 1790816400000

    cron = admin.normalize_schedule(
        {"type": "cron", "expression": " 7  *  * * * ", "timeZone": "Asia/Bangkok"}
    )
    assert cron == {"type": "cron", "expression": "7 * * * *", "timeZone": "Asia/Bangkok"}


@pytest.mark.parametrize(
    ("schedule", "message"),
    [
        ({"type": "weekly", "time": "09:00"}, "once, daily, interval, or cron"),
        ({"type": "interval", "everyMinutes": 60}, "anchorAt"),
        ({"type": "cron", "expression": "0 9 * * *"}, "IANA"),
        ({"type": "cron", "expression": "0 9 * *", "timeZone": "UTC"}, "five-field"),
        ({"type": "once", "at": "2026-10-01T09:00:00"}, "offset"),
        ({"type": "daily", "time": "9:00"}, "HH:MM"),
    ],
)
def test_normalize_schedule_rejects_what_the_server_rejects(schedule, message) -> None:
    with pytest.raises(OpenMausBotApiError, match=message):
        admin.normalize_schedule(schedule)


@pytest.mark.parametrize(
    ("current", "requested", "loosens"),
    [
        # OpenMausBot omits unset fields: no "computer" key means Auto (may resolve local).
        ({}, {"computer": "off"}, False),
        ({}, {"computer": "browser"}, False),
        ({"computer": "off"}, {"computer": None}, True),
        ({"computer": "off"}, {"computer": "cloud"}, True),
        ({"computer": "browser"}, {"computer": "vm"}, True),
        ({"computer": "local"}, {"computer": "browser"}, False),
        ({"computer": "vm"}, {"computer": "cloud"}, False),
        # mcpServers unset = every configured server; an explicit list only narrows that.
        ({}, {"mcpServers": ["docs"]}, False),
        ({"mcpServers": []}, {"mcpServers": None}, True),
        ({"mcpServers": ["docs"]}, {"mcpServers": ["docs", "mail"]}, True),
        ({"mcpServers": ["docs", "mail"]}, {"mcpServers": ["docs"]}, False),
        # composio / browser are on unless explicitly false.
        ({}, {"composio": False}, False),
        ({}, {"composio": True}, False),
        ({"composio": False}, {"composio": True}, True),
        ({"browser": False}, {"browser": True}, True),
        ({"browser": True}, {"browser": False}, False),
        # Any working-folder move needs confirmation.
        ({"cwd": "/work/a"}, {"cwd": "/work/b"}, True),
        ({"cwd": "/work/a"}, {"cwd": "/work/a/"}, False),
        ({}, {"cwd": None}, False),
        ({}, {"cwd": "/work/a"}, True),
    ],
)
def test_loosening_uses_app_semantics_for_unset_fields(current, requested, loosens) -> None:
    from openmausbot_cua_mcp.guards import loosening

    assert bool(loosening(current, requested)) is loosens


def test_computer_values_match_the_app() -> None:
    for value in ("off", "browser", "cloud", "vm", "local", None):
        assert validate_bot_patch({"computer": value}) == {"computer": value}
    with pytest.raises(OpenMausBotApiError, match="null \\(Auto\\)"):
        validate_bot_patch({"computer": "remote"})
    assert validate_bot_patch({"mcpServers": None}) == {"mcpServers": None}


def test_switch_and_cwd_values_are_validated() -> None:
    for field in ("composio", "browser"):
        assert validate_bot_patch({field: False}) == {field: False}
        with pytest.raises(OpenMausBotApiError, match="true or false"):
            validate_bot_patch({field: "off"})
    assert validate_bot_patch({"cwd": "/work/a/../b/"}) == {"cwd": "/work/b"}
    assert validate_bot_patch({"cwd": " "}) == {"cwd": None}
    assert validate_bot_patch({"cwd": None}) == {"cwd": None}
    with pytest.raises(OpenMausBotApiError, match="absolute path"):
        validate_bot_patch({"cwd": "relative/dir"})


def test_turning_everything_off_needs_no_confirmation_and_reads_back(
    fake_api, monkeypatch
) -> None:
    state, origin = fake_api
    monkeypatch.setenv("OPENMAUSBOT_TOKEN", "paired-session")
    bot = {"id": "bot-1", "name": "research-bot"}  # all switches unset = widest
    _configure_bot(state, bot)
    patch = {"computer": "off", "browser": False, "composio": False, "mcpServers": []}

    def patched(request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        bot.update(request["body"])
        return 200, {"bot": bot}

    state["responses"][("PATCH", "/api/bots/bot-1")] = patched
    result = admin.update_bot(ApiClient(origin), "bot-1", patch, dry_run=False)
    assert result["verified"] is True
    assert result["request"]["body"] == patch


def test_moving_the_working_folder_needs_confirmation(fake_api) -> None:
    state, origin = fake_api
    _configure_bot(state, {"id": "bot-1", "name": "dev-bot", "cwd": "/work/main"})
    client = ApiClient(origin)
    with pytest.raises(OpenMausBotApiError, match="working folder changes"):
        admin.update_bot(client, "bot-1", {"cwd": "/work/sandbox"})
    planned = admin.update_bot(client, "bot-1", {"cwd": "/work/sandbox"}, allow_loosen=True)
    assert planned["request"]["body"] == {"cwd": "/work/sandbox"}


def test_allowlist_refuses_mcp_reset_to_default(monkeypatch) -> None:
    from openmausbot_cua_mcp.guards import enforce_mcp_allowlist

    monkeypatch.setenv("OPENMAUSBOT_MCP_ALLOWLIST", "docs")
    with pytest.raises(OpenMausBotApiError, match="cannot bound"):
        enforce_mcp_allowlist(None)
    enforce_mcp_allowlist(["docs"])


def test_tightening_an_auto_bot_needs_no_confirmation(fake_api) -> None:
    state, origin = fake_api
    _configure_bot(state, {"id": "bot-1", "name": "research-bot"})  # computer unset = Auto
    result = admin.update_bot(ApiClient(origin), "bot-1", {"computer": "off"})
    assert result["action"] == "update"
    assert result["request"]["body"] == {"computer": "off"}
