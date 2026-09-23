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
        raise OpenMausBotApiError(
            f"Ambiguous {kind} name {value!r}; use its exact id instead."
        )
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
    validate_bot_patch(patch)
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
    result = _base_result(
        "delete", dry_run, "DELETE", path, {}, {"id": routine_id, "name": name}
    )
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
