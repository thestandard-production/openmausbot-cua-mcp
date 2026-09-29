"""Shared orchestration for guarded OpenMausBot administration writes."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

from .api import ApiClient, OpenMausBotApiError
from .guards import enforce_mcp_allowlist, loosening, validate_bot_patch

ROUTINE_FIELDS = {
    "name",
    "prompt",
    "bot",
    "target",
    "groupId",
    "runOn",
    "enabled",
    "schedule",
    "durationMinutes",
    "timeoutMinutes",
    "overlap",
    "continuity",
    "resultsThreadId",
}


def summarize_soul(value: str) -> dict[str, Any]:
    """Return a stable, non-secret description of soul text."""
    encoded = value.encode("utf-8")
    return {"sha256": hashlib.sha256(encoded).hexdigest(), "bytes": len(encoded)}


def safe_value(value: Any) -> Any:
    """Replace soul text recursively before returning administration data."""
    if isinstance(value, dict):
        return {
            key: (
                summarize_soul(item)
                if key == "soul" and isinstance(item, str)
                else safe_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [safe_value(item) for item in value]
    return value


def _ensure_compatible(client: ApiClient) -> None:
    compatibility = client.check_compatibility()
    if not compatibility.get("ok"):
        raise OpenMausBotApiError(
            "The configured API did not identify itself as OpenMausBot; refusing administration."
        )


def _items(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(value, list):
        raise OpenMausBotApiError(f"OpenMausBot returned an invalid {key} list.")
    return [item for item in value if isinstance(item, dict)]


def _resolve_named(
    items: list[dict[str, Any]],
    value: str,
    *,
    kind: str,
) -> dict[str, Any]:
    exact_id = [item for item in items if item.get("id") == value]
    if exact_id:
        return exact_id[0]
    matches = [item for item in items if item.get("name") == value]
    if not matches:
        raise OpenMausBotApiError(f"{kind.capitalize()} not found: {value}.")
    if len(matches) > 1:
        raise OpenMausBotApiError(f"Ambiguous {kind} name {value!r}; use its exact id instead.")
    return matches[0]


def _bots(client: ApiClient) -> list[dict[str, Any]]:
    return _items(client.list_bots(include_soul=True), "bots")


def _routines_and_runs(client: ApiClient) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    payload = client.list_routines()
    return _items(payload, "routines"), _items(payload, "runs")


def _relevant(item: dict[str, Any], fields: Any) -> dict[str, Any]:
    return safe_value({field: item.get(field) for field in fields})


def _base_result(
    action: str,
    dry_run: bool,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    before: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "ok": True,
        "action": action,
        "dry_run": dry_run,
        "request": {"method": method, "path": path, "body": safe_value(body or {})},
        "before": safe_value(before),
    }


def _finish(
    result: dict[str, Any],
    after: dict[str, Any] | None,
    mismatches: list[str],
) -> dict[str, Any]:
    result.update(
        {
            "after": safe_value(after),
            "verified": not mismatches,
            "mismatches": mismatches,
        }
    )
    if mismatches:
        result.update({"ok": False, "status": "unknown"})
    return result


def _mismatches(after: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    return [field for field, value in expected.items() if after.get(field) != value]


def _bot_by_id(client: ApiClient, bot_id: str) -> dict[str, Any] | None:
    return next((bot for bot in _bots(client) if bot.get("id") == bot_id), None)


def update_bot(
    client: ApiClient,
    bot_id_or_name: str,
    patch: dict[str, Any],
    allow_loosen: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Update only changed bot fields with access-widening guards."""
    _ensure_compatible(client)
    patch = validate_bot_patch(patch)
    bot = _resolve_named(_bots(client), bot_id_or_name, kind="bot")
    if "mcpServers" in patch:
        enforce_mcp_allowlist(patch["mcpServers"])
    reasons = loosening(bot, patch)
    if reasons and not allow_loosen:
        raise OpenMausBotApiError(
            "This bot update loosens access; pass allow_loosen=True to confirm: "
            f"{'; '.join(reasons)}."
        )
    if "modelSelection" in patch:
        if bot.get("busy") is True or bot.get("status") == "busy":
            raise OpenMausBotApiError("The bot is busy; wait before changing its model.")
        selection = patch["modelSelection"]
        patch = {
            **patch,
            "modelSelection": client.validate_model_selection(
                selection["instanceId"], selection["model"], selection.get("effort")
            ),
        }

    changed = {field: value for field, value in patch.items() if bot.get(field) != value}
    bot_id = bot.get("id")
    if not isinstance(bot_id, str) or not bot_id:
        raise OpenMausBotApiError("The selected bot has no usable id.")
    method = "PATCH"
    path = f"/api/bots/{bot_id}"
    before = _relevant(bot, patch)
    if not changed:
        return _base_result("noop", dry_run, method, path, {}, before)
    request_body = dict(changed)
    if "modelSelection" in changed:
        request_body["requireAvailableModel"] = True
    result = _base_result("update", dry_run, method, path, request_body, before)
    if dry_run:
        return result

    client.patch_bot(bot_id, request_body)
    after_bot = _bot_by_id(client, bot_id)
    if after_bot is None:
        return _finish(result, None, ["bot missing after update"])
    expected = changed
    return _finish(result, _relevant(after_bot, patch), _mismatches(after_bot, expected))


def set_bot_model(
    client: ApiClient,
    bot: str,
    instance_id: str,
    model: str,
    effort: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Validate and set a bot's model selection."""
    _ensure_compatible(client)
    current = _resolve_named(_bots(client), bot, kind="bot")
    if current.get("busy") is True or current.get("status") == "busy":
        raise OpenMausBotApiError("The bot is busy; wait before changing its model.")
    selection = client.validate_model_selection(instance_id, model, effort)
    bot_id = current.get("id")
    if not isinstance(bot_id, str) or not bot_id:
        raise OpenMausBotApiError("The selected bot has no usable id.")
    return update_bot(client, bot_id, {"modelSelection": selection}, dry_run=dry_run)


TEAM_NAME_MAX_CHARS = 60
GENERAL_TEAM = "General"


def _team_key(section: Any) -> str:
    """OpenMausBot's section identity: trimmed label, empty for the unsectioned General team."""
    return section.strip() if isinstance(section, str) else ""


def _can_access_team(bot: dict[str, Any], section: str) -> bool:
    """Mirror of OpenMausBot's canAccessTeam (peer-roster.js)."""
    if section == _team_key(bot.get("section")):
        return True
    managed = bot.get("managedSections")
    return bool(
        bot.get("chiefOfStaff")
        and isinstance(managed, list)
        and any(isinstance(value, str) and _team_key(value) == section for value in managed)
    )


def _reach(bots: list[dict[str, Any]], sections: dict[str, str]) -> set[tuple[str, str]]:
    """Directed (from, to) name pairs that can message each other under `sections`.

    Mirrors canReachPeer except the shared-audience rule, which a team move does not change.
    """
    pairs = set()
    for source in bots:
        placed = {**source, "section": sections[source["id"]]}
        peers = source.get("peers")
        for target in bots:
            if target["id"] == source["id"] or target.get("hidden"):
                continue
            if isinstance(peers, list) and target["id"] not in peers:
                continue
            if _can_access_team(placed, sections[target["id"]]):
                pairs.add((source.get("name") or source["id"], target.get("name") or target["id"]))
    return pairs


def _chief_covered(bots: list[dict[str, Any]], sections: dict[str, str]) -> set[str]:
    """Bots whose failures reach a Chief of Staff in their own team (the Chief itself excluded)."""
    covered = set()
    for bot in bots:
        team = sections[bot["id"]]
        if any(
            other.get("chiefOfStaff") and other["id"] != bot["id"] and sections[other["id"]] == team
            for other in bots
        ):
            covered.add(bot.get("name") or bot["id"])
    return covered


def _team_label(section: str) -> str:
    return section or GENERAL_TEAM


def list_teams(client: ApiClient) -> dict[str, Any]:
    """Return each team with its members and Chief of Staff."""
    bots = _bots(client)
    named = client.list_teams().get("sections")
    teams: dict[str, dict[str, Any]] = {}
    for section in [""] + [_team_key(item) for item in named or [] if _team_key(item)]:
        teams.setdefault(section, {"team": _team_label(section), "members": [], "chief": None})
    for bot in bots:
        if bot.get("hidden"):
            continue
        entry = teams.setdefault(
            _team_key(bot.get("section")),
            {"team": _team_label(_team_key(bot.get("section"))), "members": [], "chief": None},
        )
        entry["members"].append(bot.get("name"))
        if bot.get("chiefOfStaff"):
            entry["chief"] = bot.get("name")
    return {"ok": True, "teams": list(teams.values())}


def set_team(
    client: ApiClient,
    team: str,
    add: list[str] | None = None,
    remove: list[str] | None = None,
    allow_reach_change: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Create a team or move bots in and out of it, refusing silent changes to who reaches whom.

    A team is not only a sidebar heading: bots in different teams cannot message each other
    and a bot's failures only reach the Chief of Staff of its own team.
    """
    _ensure_compatible(client)
    if not isinstance(team, str) or not team.strip():
        raise OpenMausBotApiError("Team name is required; bots leave a team with remove.")
    name = team.strip()
    if len(name) > TEAM_NAME_MAX_CHARS:
        raise OpenMausBotApiError(f"Team name must be at most {TEAM_NAME_MAX_CHARS} characters.")
    add, remove = list(add or []), list(remove or [])
    if not add and not remove:
        raise OpenMausBotApiError("Name at least one bot to add or remove.")

    bots = [bot for bot in _bots(client) if isinstance(bot.get("id"), str) and bot["id"]]
    to_add = [_resolve_named(bots, value, kind="bot") for value in add]
    to_remove = [_resolve_named(bots, value, kind="bot") for value in remove]
    overlap = {bot["id"] for bot in to_add} & {bot["id"] for bot in to_remove}
    if overlap:
        raise OpenMausBotApiError("A bot cannot be added to and removed from a team at once.")

    existing = {_team_key(item) for item in client.list_teams().get("sections") or []}
    before = {bot["id"]: _team_key(bot.get("section")) for bot in bots}
    after = dict(before)
    add_ids = [bot["id"] for bot in to_add if before[bot["id"]] != name]
    remove_ids = [bot["id"] for bot in to_remove if before[bot["id"]] == name]
    for bot_id in add_ids:
        after[bot_id] = name
    for bot_id in remove_ids:
        after[bot_id] = ""

    by_id = {bot["id"]: bot for bot in bots}
    chiefs = [
        by_id[bot_id].get("name")
        for bot_id in after
        if after[bot_id] == name and by_id[bot_id].get("chiefOfStaff")
    ]
    if len(chiefs) > 1:
        raise OpenMausBotApiError(
            f"A team can have only one Chief of Staff; {name} would have {', '.join(chiefs)}."
        )

    reach_before, reach_after = _reach(bots, before), _reach(bots, after)
    covered_before, covered_after = _chief_covered(bots, before), _chief_covered(bots, after)
    impact = {
        "moves": [
            {
                "bot": by_id[bot_id].get("name"),
                "from": _team_label(before[bot_id]),
                "to": _team_label(after[bot_id]),
            }
            for bot_id in add_ids + remove_ids
        ],
        "reach_lost": sorted(f"{a} -> {b}" for a, b in reach_before - reach_after),
        "reach_gained": sorted(f"{a} -> {b}" for a, b in reach_after - reach_before),
        "chief_coverage_lost": sorted(covered_before - covered_after),
        "chief_coverage_gained": sorted(covered_after - covered_before),
    }

    if name in existing:
        method, path = "PUT", f"/api/sidebar-sections?section={name}"
        body: dict[str, Any] = {"addBotIds": add_ids, "removeBotIds": remove_ids}
    else:
        if remove_ids:
            raise OpenMausBotApiError(f"Team {name} does not exist; nothing to remove from it.")
        method, path = "POST", "/api/sidebar-sections"
        body = {"name": name, "botIds": add_ids}
    teams_before = {
        by_id[bot_id].get("name"): _team_label(before[bot_id]) for bot_id in add_ids + remove_ids
    }
    if not add_ids and not remove_ids:
        result = _base_result("noop", dry_run, method, path, {}, teams_before)
        result["impact"] = impact
        return result
    result = _base_result(
        "create_team" if method == "POST" else "update_team",
        dry_run,
        method,
        path,
        body,
        teams_before,
    )
    result["impact"] = impact
    changes_reach = any(
        impact[key]
        for key in ("reach_lost", "reach_gained", "chief_coverage_lost", "chief_coverage_gained")
    )
    if changes_reach and not allow_reach_change:
        if dry_run:
            result["warning"] = (
                "This move changes which bots can reach each other or a Chief of Staff; "
                "applying it requires allow_reach_change=True."
            )
            return result
        raise OpenMausBotApiError(
            "This team change alters which bots can reach each other or a Chief of Staff; "
            "review the dry run and pass allow_reach_change=True to confirm."
        )
    if dry_run:
        return result

    if method == "POST":
        client.create_team(name, add_ids)
    else:
        client.update_team_members(name, add_ids, remove_ids)
    fresh = {bot.get("id"): bot for bot in _bots(client)}
    mismatches = [
        by_id[bot_id].get("name") or bot_id
        for bot_id in add_ids + remove_ids
        if bot_id not in fresh or _team_key(fresh[bot_id].get("section")) != after[bot_id]
    ]
    teams_after = {
        by_id[bot_id].get("name"): _team_label(_team_key(fresh.get(bot_id, {}).get("section")))
        for bot_id in add_ids + remove_ids
    }
    return _finish(result, teams_after, mismatches)


def _bounded_string(value: Any, field: str, maximum: int) -> str:
    """Trim like the server does and enforce presence and length."""
    if not isinstance(value, str) or not value.strip():
        raise OpenMausBotApiError(f"Routine {field} must be a non-empty string.")
    cleaned = value.strip()
    if len(cleaned) > maximum:
        raise OpenMausBotApiError(f"Routine {field} must be at most {maximum} characters.")
    return cleaned


WEEKDAY_NUMBER = {
    "sunday": 0,
    "monday": 1,
    "tuesday": 2,
    "wednesday": 3,
    "thursday": 4,
    "friday": 5,
    "saturday": 6,
}
ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]
_CLOCK = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
_IANA = re.compile(r"[A-Za-z][A-Za-z0-9_+-]*(?:/[A-Za-z0-9_+-]+)+")


def _epoch_ms(value: Any, field: str) -> int:
    """Accept epoch milliseconds or an RFC3339 timestamp with offset; return epoch ms."""
    if isinstance(value, int) and not isinstance(value, bool):
        if value < 0:
            raise OpenMausBotApiError(f"{field} must not be negative.")
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise OpenMausBotApiError(
                f"{field} must be epoch milliseconds or RFC3339 with an offset."
            ) from exc
        if parsed.utcoffset() is None:
            raise OpenMausBotApiError(f"{field} must include a timezone offset.")
        return int(parsed.timestamp() * 1000)
    raise OpenMausBotApiError(f"{field} must be epoch milliseconds or RFC3339 with an offset.")


def _weekday_numbers(value: Any, field: str) -> list[int]:
    if not isinstance(value, list):
        raise OpenMausBotApiError(f"{field} must be a list.")
    days: list[int] = []
    for day in value:
        if isinstance(day, str) and day.lower() in WEEKDAY_NUMBER:
            days.append(WEEKDAY_NUMBER[day.lower()])
        elif isinstance(day, int) and not isinstance(day, bool) and 0 <= day <= 6:
            days.append(day)
        else:
            raise OpenMausBotApiError(f"{field} entries must be 0-6 or weekday names.")
    return days


def normalize_schedule(schedule: Any) -> dict[str, Any]:
    """Validate a schedule and return the canonical shape OpenMausBot stores.

    Mirrors the app's public-API parser so a read-back compares equal and an unchanged
    desired state plans as a no-op: once/interval times are epoch milliseconds, daily
    weekdays are sorted unique numbers (all seven when omitted), cron needs an IANA
    timeZone and has its whitespace collapsed.
    """
    if not isinstance(schedule, dict):
        raise OpenMausBotApiError("Routine schedule must be a JSON object.")
    kind = schedule.get("type")
    if kind == "once":
        return {"type": "once", "at": _epoch_ms(schedule.get("at"), "schedule.at")}
    if kind == "daily":
        time_value = schedule.get("time")
        if not isinstance(time_value, str) or not _CLOCK.fullmatch(time_value):
            raise OpenMausBotApiError("schedule.time must use HH:MM in 24-hour time.")
        raw_days = schedule.get("weekdays")
        days = sorted(set(_weekday_numbers(raw_days, "schedule.weekdays"))) if raw_days else []
        return {"type": "daily", "time": time_value, "weekdays": days or list(ALL_DAYS)}
    if kind == "interval":
        every = schedule.get("everyMinutes")
        if isinstance(every, bool) or not isinstance(every, int) or not 5 <= every <= 1440:
            raise OpenMausBotApiError(
                "schedule.everyMinutes must be a whole number from 5 to 1440."
            )
        if "anchorAt" not in schedule:
            raise OpenMausBotApiError("Interval schedules require anchorAt (the first run time).")
        result: dict[str, Any] = {
            "type": "interval",
            "everyMinutes": every,
            "anchorAt": _epoch_ms(schedule["anchorAt"], "schedule.anchorAt"),
        }
        if schedule.get("weekdays") is not None:
            days = _weekday_numbers(schedule["weekdays"], "schedule.weekdays")
            if not days or len(set(days)) != len(days):
                raise OpenMausBotApiError("schedule.weekdays must list each day at most once.")
            if len(days) < len(ALL_DAYS):
                result["weekdays"] = sorted(days)
        if schedule.get("window") is not None:
            window = schedule["window"]
            if (
                not isinstance(window, dict)
                or not _CLOCK.fullmatch(str(window.get("start", "")))
                or not _CLOCK.fullmatch(str(window.get("end", "")))
                or window["start"] >= window["end"]
            ):
                raise OpenMausBotApiError("schedule.window needs start < end as HH:MM.")
            result["window"] = {"start": window["start"], "end": window["end"]}
        if schedule.get("endsAt") is not None:
            ends = _epoch_ms(schedule["endsAt"], "schedule.endsAt")
            if ends < result["anchorAt"]:
                raise OpenMausBotApiError("schedule.endsAt must not be before anchorAt.")
            result["endsAt"] = ends
        return result
    if kind == "cron":
        extra = sorted(set(schedule) - {"type", "expression", "timeZone"})
        if extra:
            raise OpenMausBotApiError(
                "Cron schedules accept only type, expression and timeZone "
                f"(got {', '.join(extra)})."
            )
        expression = schedule.get("expression")
        if not isinstance(expression, str):
            raise OpenMausBotApiError("schedule.expression must be a five-field cron string.")
        expression = " ".join(expression.split())
        if len(expression.split(" ")) != 5 or "@" in expression:
            raise OpenMausBotApiError("schedule.expression must be a five-field cron string.")
        zone = schedule.get("timeZone")
        zone = zone.strip() if isinstance(zone, str) else ""
        if zone != "UTC" and not _IANA.fullmatch(zone):
            raise OpenMausBotApiError(
                "schedule.timeZone must be an IANA zone such as Asia/Bangkok."
            )
        return {"type": "cron", "expression": expression, "timeZone": zone}
    raise OpenMausBotApiError("Routine schedule type must be once, daily, interval, or cron.")


def _routine_body(client: ApiClient, spec: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise OpenMausBotApiError("Routine spec must be a JSON object.")
    unknown = sorted(set(spec) - ROUTINE_FIELDS)
    if unknown:
        raise OpenMausBotApiError(f"Unknown routine field(s): {', '.join(unknown)}.")
    for field in ("name", "prompt", "bot"):
        if field not in spec:
            raise OpenMausBotApiError(f"Routine spec requires {field}.")
    name = _bounded_string(spec["name"], "name", 80)
    prompt = _bounded_string(spec["prompt"], "prompt", 20_000)
    bot_ref = _bounded_string(spec["bot"], "bot", 500)
    bot = _resolve_named(_bots(client), bot_ref, kind="bot")
    bot_id = bot.get("id")
    if not isinstance(bot_id, str) or not bot_id:
        raise OpenMausBotApiError("The selected bot has no usable id.")

    if spec.get("target", "bot") not in {"bot", "room-goal"}:
        raise OpenMausBotApiError("Routine target must be bot or room-goal.")
    if spec.get("target") == "room-goal" and not isinstance(spec.get("groupId"), str):
        raise OpenMausBotApiError("Routine target room-goal requires groupId.")
    if spec.get("runOn", "maus") not in {"maus", "cloud"}:
        raise OpenMausBotApiError("Routine runOn must be maus or cloud.")
    if "enabled" in spec and not isinstance(spec["enabled"], bool):
        raise OpenMausBotApiError("Routine enabled must be a boolean.")
    if "continuity" in spec and not isinstance(spec["continuity"], bool):
        raise OpenMausBotApiError("Routine continuity must be a boolean.")
    if "overlap" in spec and spec["overlap"] not in {"skip", "queue"}:
        raise OpenMausBotApiError("Routine overlap must be skip or queue.")
    if "schedule" not in spec:
        raise OpenMausBotApiError("Routine spec requires schedule.")
    schedule = normalize_schedule(spec["schedule"])
    duration = spec.get("durationMinutes", 30)
    if isinstance(duration, bool) or not isinstance(duration, int) or not 5 <= duration <= 240:
        raise OpenMausBotApiError("Routine durationMinutes must be from 5 to 240.")
    if "timeoutMinutes" in spec:
        timeout = spec["timeoutMinutes"]
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise OpenMausBotApiError("Routine timeoutMinutes must be a positive integer.")

    body = {"botId": bot_id, **{key: value for key, value in spec.items() if key != "bot"}}
    body.update({"name": name, "prompt": prompt, "schedule": schedule})
    return body


def _routine_by_id(client: ApiClient, routine_id: str) -> dict[str, Any] | None:
    routines, _runs = _routines_and_runs(client)
    return next((routine for routine in routines if routine.get("id") == routine_id), None)


def upsert_routine(
    client: ApiClient,
    spec: dict[str, Any],
    dry_run: bool = True,
) -> dict[str, Any]:
    """Create or update a routine, using its exact name as the idempotency key."""
    _ensure_compatible(client)
    body = _routine_body(client, spec)
    routines, _runs = _routines_and_runs(client)
    matches = [routine for routine in routines if routine.get("name") == body["name"]]
    if len(matches) > 1:
        raise OpenMausBotApiError("duplicate routine names — resolve in the app first")
    if not matches:
        result = _base_result("create", dry_run, "POST", "/api/routines", body, None)
        if dry_run:
            return result
        response = client.create_routine(body)
        created = response.get("routine") if isinstance(response, dict) else None
        created_id = created.get("id") if isinstance(created, dict) else None
        after = _routine_by_id(client, created_id) if isinstance(created_id, str) else None
        if after is None:
            fresh, _runs = _routines_and_runs(client)
            fresh_matches = [item for item in fresh if item.get("name") == body["name"]]
            after = fresh_matches[0] if len(fresh_matches) == 1 else None
        if after is None:
            return _finish(result, None, ["routine missing after create"])
        return _finish(result, after, _mismatches(after, body))

    current = matches[0]
    routine_id = current.get("id")
    if not isinstance(routine_id, str) or not routine_id:
        raise OpenMausBotApiError("The selected routine has no usable id.")
    changed = {field: value for field, value in body.items() if current.get(field) != value}
    before = _relevant(current, body)
    path = f"/api/routines/{routine_id}"
    if not changed:
        return _base_result("noop", dry_run, "PATCH", path, {}, before)
    result = _base_result("update", dry_run, "PATCH", path, changed, before)
    if dry_run:
        return result
    client.update_routine(routine_id, changed)
    after = _routine_by_id(client, routine_id)
    if after is None:
        return _finish(result, None, ["routine missing after update"])
    return _finish(result, _relevant(after, body), _mismatches(after, changed))


def set_routine_enabled(
    client: ApiClient,
    name_or_id: str,
    enabled: bool,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Enable or disable one routine."""
    _ensure_compatible(client)
    if not isinstance(enabled, bool):
        raise OpenMausBotApiError("Routine enabled must be a boolean.")
    routines, _runs = _routines_and_runs(client)
    routine = _resolve_named(routines, name_or_id, kind="routine")
    routine_id = routine.get("id")
    if not isinstance(routine_id, str) or not routine_id:
        raise OpenMausBotApiError("The selected routine has no usable id.")
    path = f"/api/routines/{routine_id}"
    before = {"enabled": routine.get("enabled")}
    if routine.get("enabled") == enabled:
        return _base_result("noop", dry_run, "PATCH", path, {}, before)
    result = _base_result("update", dry_run, "PATCH", path, {"enabled": enabled}, before)
    if dry_run:
        return result
    client.update_routine(routine_id, {"enabled": enabled})
    after = _routine_by_id(client, routine_id)
    if after is None:
        return _finish(result, None, ["routine missing after update"])
    return _finish(
        result,
        {"enabled": after.get("enabled")},
        _mismatches(after, {"enabled": enabled}),
    )


def run_routine_now(
    client: ApiClient,
    name_or_id: str,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Start one routine immediately."""
    _ensure_compatible(client)
    routines, _runs = _routines_and_runs(client)
    routine = _resolve_named(routines, name_or_id, kind="routine")
    routine_id = routine.get("id")
    if not isinstance(routine_id, str) or not routine_id:
        raise OpenMausBotApiError("The selected routine has no usable id.")
    path = f"/api/routines/{routine_id}/run"
    before = {"id": routine_id, "name": routine.get("name")}
    result = _base_result("run", dry_run, "POST", path, {}, before)
    if dry_run:
        return result
    response = client.run_routine(routine_id)
    started = response.get("run") if isinstance(response, dict) else None
    run_id = started.get("id") if isinstance(started, dict) else None
    _routines, runs = _routines_and_runs(client)
    after = next((run for run in runs if run.get("id") == run_id), None)
    if after is None:
        return _finish(result, None, ["run missing after start"])
    expected = {"routineId": routine_id} if "routineId" in after else {}
    return _finish(result, after, _mismatches(after, expected))


def delete_routine(
    client: ApiClient,
    name_or_id: str,
    confirm_name: str,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Delete one routine after exact-name confirmation."""
    _ensure_compatible(client)
    routines, _runs = _routines_and_runs(client)
    routine = _resolve_named(routines, name_or_id, kind="routine")
    name = routine.get("name")
    if confirm_name != name:
        raise OpenMausBotApiError("confirm_name must exactly match the routine's current name.")
    routine_id = routine.get("id")
    if not isinstance(routine_id, str) or not routine_id:
        raise OpenMausBotApiError("The selected routine has no usable id.")
    path = f"/api/routines/{routine_id}"
    result = _base_result("delete", dry_run, "DELETE", path, {}, {"id": routine_id, "name": name})
    if dry_run:
        return result
    client.delete_routine(routine_id)
    after = _routine_by_id(client, routine_id)
    return _finish(result, after, [] if after is None else ["routine still exists after delete"])


def cancel_run(client: ApiClient, run_id: str, dry_run: bool = True) -> dict[str, Any]:
    """Cancel one known routine run."""
    _ensure_compatible(client)
    _routines, runs = _routines_and_runs(client)
    run = next((item for item in runs if item.get("id") == run_id), None)
    if run is None:
        raise OpenMausBotApiError(f"Routine run not found: {run_id}.")
    path = f"/api/routine-runs/{run_id}/cancel"
    before = {key: run.get(key) for key in ("id", "routineId", "status")}
    result = _base_result("cancel", dry_run, "POST", path, {}, before)
    if dry_run:
        return result
    response = client.cancel_run(run_id)
    returned = response.get("run") if isinstance(response, dict) else None
    _routines, fresh_runs = _routines_and_runs(client)
    after = next((item for item in fresh_runs if item.get("id") == run_id), None)
    if after is None:
        return _finish(result, None, ["run missing after cancel"])
    expected = {}
    if isinstance(returned, dict) and "status" in returned:
        expected["status"] = returned["status"]
    return _finish(result, after, _mismatches(after, expected))
