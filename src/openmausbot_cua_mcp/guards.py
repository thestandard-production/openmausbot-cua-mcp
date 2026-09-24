"""Validation and safety guards for OpenMausBot administration writes."""

from __future__ import annotations

import os
from typing import Any

from .api import OpenMausBotApiError

BOT_FIELDS = {
    "name",
    "title",
    "description",
    "soul",
    "modelSelection",
    "computer",
    "mcpServers",
    "composio",
    "browser",
    "cwd",
    "approvalMode",
}
SOUL_MAX_BYTES = 24_000
CWD_MAX_CHARS = 4096

# Switches OpenMausBot treats as on unless explicitly false: `composio` gives the bot every
# connected app (one shared account session, not per app) and `browser` the built-in browser.
ON_UNLESS_FALSE = {"composio": "connected apps (composio)", "browser": "built-in browser"}

# Values OpenMausBot accepts for a bot's computer setting (0.1.85 and 0.1.86). `None` (JSON null)
# clears the setting, which the app treats as "Auto": availability is decided when a task starts,
# so it may end up on the local computer. Rank = how much reach the setting grants; Auto is ranked
# with "local" because it can resolve to it.
COMPUTER_RANK: dict[str | None, int] = {
    "off": 0,
    "browser": 1,
    "cloud": 2,
    "vm": 2,
    "local": 3,
    None: 3,
}


def _bounded_string(value: Any, field: str, maximum: int) -> None:
    if not isinstance(value, str):
        raise OpenMausBotApiError(f"Bot {field} must be a string.")
    if len(value) > maximum:
        raise OpenMausBotApiError(f"Bot {field} must be at most {maximum} characters.")


def normalize_cwd(value: Any) -> str | None:
    """Return the working folder the server will store, or None to clear it."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise OpenMausBotApiError("Bot cwd must be an absolute path or null.")
    trimmed = value.strip()
    if not trimmed:
        return None
    home_relative = trimmed == "~" or trimmed.startswith("~/")
    expanded = os.path.expanduser(trimmed) if home_relative else trimmed
    if not os.path.isabs(expanded):
        raise OpenMausBotApiError("Bot cwd must be an absolute path or null.")
    if len(expanded) > CWD_MAX_CHARS:
        raise OpenMausBotApiError(f"Bot cwd must be at most {CWD_MAX_CHARS} characters.")
    return os.path.normpath(expanded)


def validate_bot_patch(patch: Any) -> dict[str, Any]:
    """Validate a bot patch and return it with `cwd` normalized the way the server stores it."""
    if not isinstance(patch, dict):
        raise OpenMausBotApiError("Bot patch must be a JSON object.")
    if "approvalMode" in patch and patch["approvalMode"] != "ask":
        raise OpenMausBotApiError(
            "omb-ctl only sets a bot's approvalMode to 'ask'; loosen approvals in the "
            "OpenMausBot app, where each change is seen by a person."
        )
    unknown = sorted(set(patch) - BOT_FIELDS)
    if unknown:
        raise OpenMausBotApiError(f"Unknown bot field(s): {', '.join(unknown)}.")

    limits = {"name": 100, "title": 200, "description": 4000}
    for field, maximum in limits.items():
        if field in patch:
            _bounded_string(patch[field], field, maximum)
    if "soul" in patch:
        soul = patch["soul"]
        if not isinstance(soul, str):
            raise OpenMausBotApiError("Bot soul must be a string.")
        size = len(soul.encode("utf-8"))
        if size > SOUL_MAX_BYTES:
            raise OpenMausBotApiError(
                f"Bot soul is {size} UTF-8 bytes; maximum is {SOUL_MAX_BYTES}."
            )
    if "computer" in patch and patch["computer"] not in COMPUTER_RANK:
        raise OpenMausBotApiError(
            "Bot computer must be off, browser, cloud, vm, local, or null (Auto)."
        )
    if "mcpServers" in patch and patch["mcpServers"] is not None:
        servers = patch["mcpServers"]
        if not isinstance(servers, list) or not all(
            isinstance(server, str) and server for server in servers
        ):
            raise OpenMausBotApiError("Bot mcpServers must be a list of non-empty strings.")
        if len(set(servers)) != len(servers):
            raise OpenMausBotApiError("Bot mcpServers must not contain duplicates.")
    if "modelSelection" in patch:
        selection = patch["modelSelection"]
        if not isinstance(selection, dict):
            raise OpenMausBotApiError("Bot modelSelection must be a JSON object.")
        unknown_selection = sorted(set(selection) - {"instanceId", "model", "effort"})
        if unknown_selection:
            raise OpenMausBotApiError(
                f"Unknown modelSelection field(s): {', '.join(unknown_selection)}."
            )
        for field in ("instanceId", "model"):
            if not isinstance(selection.get(field), str) or not selection[field]:
                raise OpenMausBotApiError(f"modelSelection.{field} must be a non-empty string.")
        if "effort" in selection and (
            not isinstance(selection["effort"], str) or not selection["effort"]
        ):
            raise OpenMausBotApiError("modelSelection.effort must be a non-empty string.")
    for field in ON_UNLESS_FALSE:
        if field in patch and not isinstance(patch[field], bool):
            raise OpenMausBotApiError(f"Bot {field} must be true or false.")
    if "cwd" in patch:
        return {**patch, "cwd": normalize_cwd(patch["cwd"])}
    return patch


def _computer_label(value: str | None) -> str:
    return "Auto (unset)" if value is None else value


def loosening(current_bot: dict[str, Any], patch: dict[str, Any]) -> list[str]:
    """Describe access changes that widen or move a bot's reach.

    OpenMausBot omits unset fields from bot objects, so a missing `computer`, `mcpServers`,
    `composio` or `browser` key means the setting is unset (Auto / the app default / on), not
    "off" / "none". A new working folder is listed too: it changes which files the bot touches.
    """
    reasons: list[str] = []
    for field, label in ON_UNLESS_FALSE.items():
        if field in patch and patch[field] is not False and current_bot.get(field) is False:
            reasons.append(f"{label} turns on")
    if "cwd" in patch:
        current_cwd = current_bot.get("cwd") or None
        requested_cwd = normalize_cwd(patch["cwd"])
        if requested_cwd != current_cwd:
            reasons.append(
                f"working folder changes from {current_cwd or 'unset'} to "
                f"{requested_cwd or 'unset'}"
            )
    if "computer" in patch:
        current = current_bot.get("computer")
        requested = patch["computer"]
        if COMPUTER_RANK.get(requested, 3) > COMPUTER_RANK.get(current, 3):
            reasons.append(
                f"computer changes from {_computer_label(current)} to {_computer_label(requested)}"
            )

    if "mcpServers" in patch:
        current_servers = current_bot.get("mcpServers")
        requested_servers = patch["mcpServers"]
        if requested_servers is None:
            if isinstance(current_servers, list):
                reasons.append("mcpServers reset to the app default (all configured servers)")
        elif isinstance(current_servers, list):
            existing = set(current_servers)
            for server in requested_servers:
                if server not in existing:
                    reasons.append(f"mcpServers adds {server}")
        # current unset = app default (all servers) → any explicit list narrows access
    return reasons


def enforce_mcp_allowlist(servers: list[str] | None) -> None:
    """Refuse MCP servers outside the configured allowlist, when one is set."""
    raw = os.environ.get("OPENMAUSBOT_MCP_ALLOWLIST")
    if raw is None:
        return
    if servers is None:
        raise OpenMausBotApiError(
            "mcpServers null resets to the app default (all servers), which "
            "OPENMAUSBOT_MCP_ALLOWLIST cannot bound; list the servers explicitly."
        )
    allowed = {item.strip() for item in raw.split(",") if item.strip()}
    refused = sorted(set(servers) - allowed)
    if refused:
        raise OpenMausBotApiError(
            "MCP server(s) not permitted by OPENMAUSBOT_MCP_ALLOWLIST: "
            f"{', '.join(refused)}."
        )


def writes_enabled() -> bool:
    """Return whether MCP administration writes are explicitly enabled."""
    return os.environ.get("OPENMAUSBOT_ENABLE_ADMIN_WRITES") == "1"
