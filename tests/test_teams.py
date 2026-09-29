from __future__ import annotations

from typing import Any

import pytest

from openmausbot_cua_mcp import admin
from openmausbot_cua_mcp.api import ApiClient, OpenMausBotApiError
from openmausbot_cua_mcp.cli import main


def _team_state(state: dict[str, Any], bots: list[dict[str, Any]], sections: list[str]) -> None:
    state["responses"][("GET", "/api/bots")] = lambda _request: (200, {"bots": bots})
    state["responses"][("GET", "/api/sidebar-sections")] = lambda _request: (
        200,
        {"sections": list(sections)},
    )

    def create(request):
        body = request["body"]
        sections.append(body["name"])
        for bot in bots:
            if bot["id"] in body["botIds"]:
                bot["section"] = body["name"]
        return 200, {"section": body["name"], "sections": sections}

    def update(request):
        name = request["query"].split("=", 1)[1]
        from urllib.parse import unquote_plus

        name = unquote_plus(name)
        body = request["body"]
        for bot in bots:
            if bot["id"] in body["addBotIds"]:
                bot["section"] = name
            if bot["id"] in body["removeBotIds"]:
                bot.pop("section", None)
        return 200, {"sections": sections}

    state["responses"][("POST", "/api/sidebar-sections")] = create
    state["responses"][("PUT", "/api/sidebar-sections")] = update


def _fleet() -> list[dict[str, Any]]:
    return [
        {"id": "b1", "name": "Minerva", "chiefOfStaff": True},
        {"id": "b2", "name": "Arthur"},
        {"id": "b3", "name": "Hagrid"},
        {"id": "b4", "name": "Galadriel", "section": "Filmmaker", "chiefOfStaff": True},
        {"id": "b5", "name": "Gimli", "section": "Filmmaker"},
    ]


def _mutations(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [request for request in state["requests"] if request["method"] != "GET"]


def test_moving_a_whole_team_changes_no_reach_and_applies(fake_api, monkeypatch) -> None:
    state, origin = fake_api
    monkeypatch.setenv("OPENMAUSBOT_TOKEN", "paired-session")
    bots = _fleet()
    _team_state(state, bots, ["Filmmaker"])

    plan = admin.set_team(ApiClient(origin), "Phoenix", add=["Minerva", "Arthur", "Hagrid"])
    assert plan["action"] == "create_team"
    assert plan["impact"]["reach_lost"] == []
    assert plan["impact"]["reach_gained"] == []
    assert plan["impact"]["chief_coverage_lost"] == []
    assert "warning" not in plan
    assert not _mutations(state)

    result = admin.set_team(
        ApiClient(origin), "Phoenix", add=["Minerva", "Arthur", "Hagrid"], dry_run=False
    )
    assert result["ok"] is True
    assert result["verified"] is True
    assert result["after"] == {"Minerva": "Phoenix", "Arthur": "Phoenix", "Hagrid": "Phoenix"}

    again = admin.set_team(ApiClient(origin), "Phoenix", add=["Minerva"])
    assert again["action"] == "noop"


def test_splitting_a_team_reports_lost_reach_and_refuses_without_confirmation(
    fake_api, monkeypatch
) -> None:
    state, origin = fake_api
    monkeypatch.setenv("OPENMAUSBOT_TOKEN", "paired-session")
    _team_state(state, _fleet(), ["Filmmaker"])

    plan = admin.set_team(ApiClient(origin), "Engineering", add=["Hagrid"])
    assert "Minerva -> Hagrid" in plan["impact"]["reach_lost"]
    assert plan["impact"]["chief_coverage_lost"] == ["Hagrid"]
    assert "allow_reach_change" in plan["warning"]

    with pytest.raises(OpenMausBotApiError, match="allow_reach_change"):
        admin.set_team(ApiClient(origin), "Engineering", add=["Hagrid"], dry_run=False)
    assert not _mutations(state)

    result = admin.set_team(
        ApiClient(origin),
        "Engineering",
        add=["Hagrid"],
        allow_reach_change=True,
        dry_run=False,
    )
    assert result["verified"] is True


def test_second_chief_in_one_team_is_refused(fake_api) -> None:
    state, origin = fake_api
    _team_state(state, _fleet(), ["Filmmaker"])

    with pytest.raises(OpenMausBotApiError, match="only one Chief of Staff"):
        admin.set_team(ApiClient(origin), "Filmmaker", add=["Minerva"])


def test_remove_returns_bot_to_general_via_put(fake_api, monkeypatch) -> None:
    state, origin = fake_api
    monkeypatch.setenv("OPENMAUSBOT_TOKEN", "paired-session")
    bots = _fleet()
    _team_state(state, bots, ["Filmmaker"])

    result = admin.set_team(
        ApiClient(origin), "Filmmaker", remove=["Gimli"], allow_reach_change=True, dry_run=False
    )
    assert result["request"]["method"] == "PUT"
    assert result["after"] == {"Gimli": "General"}
    assert "section" not in bots[4]


def test_list_teams_and_cli(fake_api, capsys) -> None:
    state, origin = fake_api
    _team_state(state, _fleet(), ["Filmmaker"])

    teams = admin.list_teams(ApiClient(origin))["teams"]
    assert teams[0] == {
        "team": "General",
        "members": ["Minerva", "Arthur", "Hagrid"],
        "chief": "Minerva",
    }
    assert teams[1]["chief"] == "Galadriel"

    assert main(["team", "Phoenix", "--add", "Minerva"]) == 10
    assert not _mutations(state)
