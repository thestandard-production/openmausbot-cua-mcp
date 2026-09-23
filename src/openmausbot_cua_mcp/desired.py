"""Desired-state file loading, planning, and single-item application."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import admin
from .api import ApiClient, OpenMausBotApiError

BOT_ENTRY_FIELDS = {
    "name",
    "id",
    "title",
    "description",
    "soul_file",
    "soul_append_files",
    "modelSelection",
    "computer",
    "mcpServers",
    "allow_loosen",
}
ROUTINE_ENTRY_FIELDS = (admin.ROUTINE_FIELDS - {"prompt"}) | {"prompt_file"}


def _load_document(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OpenMausBotApiError(f"Unable to read desired-state file: {exc}") from exc
    try:
        if path.suffix.lower() in {".yaml", ".yml"}:
            try:
                import yaml
            except ImportError as exc:
                raise OpenMausBotApiError(
                    "YAML desired-state files require the optional 'yaml' extra (PyYAML)."
                ) from exc
            payload = yaml.safe_load(text)
        else:
            payload = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise OpenMausBotApiError(f"Invalid desired-state file: {exc}") from exc
    if not isinstance(payload, dict):
        raise OpenMausBotApiError("Desired-state file must contain an object.")
    if payload.get("version") != 1:
        raise OpenMausBotApiError("Desired-state version must be 1.")
    unknown = sorted(set(payload) - {"version", "bots", "routines"})
    if unknown:
        raise OpenMausBotApiError(f"Unknown desired-state field(s): {', '.join(unknown)}.")
    for field in ("bots", "routines"):
        if field in payload and not isinstance(payload[field], list):
            raise OpenMausBotApiError(f"Desired-state {field} must be a list.")
    return payload


def _relative_file(base: Path, value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value:
        raise OpenMausBotApiError(f"{field} must be a non-empty relative path.")
    relative = Path(value)
    if relative.is_absolute():
        raise OpenMausBotApiError(f"{field} must be relative to the desired-state file.")
    try:
        resolved_base = base.resolve(strict=True)
        resolved = (resolved_base / relative).resolve(strict=True)
    except OSError as exc:
        raise OpenMausBotApiError(f"Unable to resolve {field}: {exc}") from exc
    if not resolved.is_relative_to(resolved_base):
        raise OpenMausBotApiError(f"{field} escapes the desired-state directory.")
    if not resolved.is_file():
        raise OpenMausBotApiError(f"{field} must point to a file.")
    return resolved


def _read_relative(base: Path, value: Any, field: str) -> str:
    path = _relative_file(base, value, field)
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise OpenMausBotApiError(f"Unable to read {field}: {exc}") from exc


def _bot_item(entry: Any, base: Path) -> tuple[str, dict[str, Any], bool]:
    if not isinstance(entry, dict):
        raise OpenMausBotApiError("Each desired bot must be an object.")
    unknown = sorted(set(entry) - BOT_ENTRY_FIELDS)
    if unknown:
        raise OpenMausBotApiError(f"Unknown desired bot field(s): {', '.join(unknown)}.")
    name = entry.get("name")
    if not isinstance(name, str) or not name:
        raise OpenMausBotApiError("Each desired bot requires a non-empty name.")
    selector = entry.get("id", name)
    if not isinstance(selector, str) or not selector:
        raise OpenMausBotApiError("Desired bot id must be a non-empty string.")
    if "allow_loosen" in entry and not isinstance(entry["allow_loosen"], bool):
        raise OpenMausBotApiError("Desired bot allow_loosen must be a boolean.")
    patch = {
        key: value
        for key, value in entry.items()
        if key in {"name", "title", "description", "modelSelection", "computer", "mcpServers"}
    }
    append_files = entry.get("soul_append_files", [])
    if not isinstance(append_files, list):
        raise OpenMausBotApiError("Desired bot soul_append_files must be a list.")
    if append_files and "soul_file" not in entry:
        raise OpenMausBotApiError("soul_append_files requires soul_file.")
    if "soul_file" in entry:
        parts = [_read_relative(base, entry["soul_file"], "soul_file")]
        parts.extend(
            _read_relative(base, value, "soul_append_files entry") for value in append_files
        )
        patch["soul"] = "\n\n".join(parts)
    return selector, patch, entry.get("allow_loosen", False)


def _routine_item(entry: Any, base: Path) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise OpenMausBotApiError("Each desired routine must be an object.")
    unknown = sorted(set(entry) - ROUTINE_ENTRY_FIELDS)
    if unknown:
        raise OpenMausBotApiError(f"Unknown desired routine field(s): {', '.join(unknown)}.")
    for field in ("name", "bot", "prompt_file"):
        if not isinstance(entry.get(field), str) or not entry[field]:
            raise OpenMausBotApiError(f"Each desired routine requires a non-empty {field}.")
    return {
        **{key: value for key, value in entry.items() if key != "prompt_file"},
        "prompt": _read_relative(base, entry["prompt_file"], "prompt_file"),
    }


def _diffs(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if result["action"] == "noop":
        return {}
    before = result.get("before") or {}
    request = result.get("request") or {}
    body = request.get("body") or {}
    return {
        field: {"before": before.get(field), "after": value}
        for field, value in body.items()
        if field != "requireAvailableModel"
    }


def _error_item(kind: str, name: str, exc: Exception) -> dict[str, Any]:
    return {"kind": kind, "name": name, "action": "error", "diffs": {}, "error": str(exc)}


def _parsed(path: str | Path) -> tuple[Path, list[dict[str, Any]], list[dict[str, Any]]]:
    desired_path = Path(path).expanduser()
    payload = _load_document(desired_path)
    bots = payload.get("bots", [])
    routines = payload.get("routines", [])
    return desired_path, bots, routines


def plan(client: ApiClient, path: str | Path) -> dict[str, Any]:
    """Plan every desired item without sending a mutation."""
    desired_path, bot_entries, routine_entries = _parsed(path)
    base = desired_path.parent
    items: list[dict[str, Any]] = []
    for entry in bot_entries:
        name = entry.get("name", "<invalid>") if isinstance(entry, dict) else "<invalid>"
        try:
            selector, patch, allow_loosen = _bot_item(entry, base)
            result = admin.update_bot(
                client, selector, patch, allow_loosen=allow_loosen, dry_run=True
            )
            items.append(
                {"kind": "bot", "name": name, "action": result["action"], "diffs": _diffs(result)}
            )
        except (OpenMausBotApiError, OSError, ValueError) as exc:
            items.append(_error_item("bot", name, exc))
    for entry in routine_entries:
        name = entry.get("name", "<invalid>") if isinstance(entry, dict) else "<invalid>"
        try:
            spec = _routine_item(entry, base)
            result = admin.upsert_routine(client, spec, dry_run=True)
            items.append(
                {
                    "kind": "routine",
                    "name": name,
                    "action": result["action"],
                    "diffs": _diffs(result),
                }
            )
        except (OpenMausBotApiError, OSError, ValueError) as exc:
            items.append(_error_item("routine", name, exc))
    has_errors = any(item["action"] == "error" for item in items)
    has_changes = any(item["action"] in {"create", "update", "delete"} for item in items)
    return {
        "ok": not has_errors,
        "path": str(desired_path),
        "items": items,
        "has_changes": has_changes,
    }


def _select(
    bot_entries: list[dict[str, Any]],
    routine_entries: list[dict[str, Any]],
    only: str,
) -> tuple[str, dict[str, Any]]:
    if ":" in only:
        kind, name = only.split(":", 1)
        if kind not in {"bot", "routine"} or not name:
            raise OpenMausBotApiError("--only must be bot:<name> or routine:<name>.")
        entries = bot_entries if kind == "bot" else routine_entries
        matches = [
            entry for entry in entries if isinstance(entry, dict) and entry.get("name") == name
        ]
    else:
        name = only
        matches_with_kind = [
            (kind, entry)
            for kind, entries in (("bot", bot_entries), ("routine", routine_entries))
            for entry in entries
            if isinstance(entry, dict) and entry.get("name") == name
        ]
        if len(matches_with_kind) != 1:
            raise OpenMausBotApiError(
                f"Desired item {name!r} is missing or ambiguous; use bot:<name> or routine:<name>."
            )
        return matches_with_kind[0]
    if len(matches) != 1:
        raise OpenMausBotApiError(f"Desired {kind} {name!r} is missing or ambiguous.")
    return kind, matches[0]


def apply(
    client: ApiClient,
    path: str | Path,
    only: str,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Apply exactly one selected desired-state item through the shared admin layer."""
    desired_path, bot_entries, routine_entries = _parsed(path)
    kind, entry = _select(bot_entries, routine_entries, only)
    if kind == "bot":
        selector, patch, allow_loosen = _bot_item(entry, desired_path.parent)
        return admin.update_bot(
            client, selector, patch, allow_loosen=allow_loosen, dry_run=dry_run
        )
    spec = _routine_item(entry, desired_path.parent)
    return admin.upsert_routine(client, spec, dry_run=dry_run)
