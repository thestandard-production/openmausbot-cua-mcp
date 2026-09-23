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
}
SOUL_MAX_BYTES = 24_000


def _bounded_string(value: Any, field: str, maximum: int) -> None:
    if not isinstance(value, str):
        raise OpenMausBotApiError(f"Bot {field} must be a string.")
    if len(value) > maximum:
        raise OpenMausBotApiError(f"Bot {field} must be at most {maximum} characters.")


def validate_bot_patch(patch: Any) -> dict[str, Any]:
    """Validate a bot patch and return it unchanged."""
    if not isinstance(patch, dict):
        raise OpenMausBotApiError("Bot patch must be a JSON object.")
    if "approvalMode" in patch:
        raise OpenMausBotApiError(
            "approvalMode is a per-thread setting, not a bot field; update it on a task/thread "
            "in the OpenMausBot app."
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
    if "computer" in patch and patch["computer"] not in {"off", "browser", "local"}:
        raise OpenMausBotApiError("Bot computer must be off, browser, or local.")
    if "mcpServers" in patch:
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
    return patch


def loosening(current_bot: dict[str, Any], patch: dict[str, Any]) -> list[str]:
    """Describe access changes that widen a bot's capabilities."""
    reasons: list[str] = []
    if "computer" in patch:
        current = current_bot.get("computer") or "off"
        requested = patch["computer"]
        if current == "off" and requested in {"browser", "local"}:
            reasons.append(f"computer changes from {current} to {requested}")
        elif current == "browser" and requested == "local":
            reasons.append("computer changes from browser to local")

    if "mcpServers" in patch:
        current_servers = current_bot.get("mcpServers")
        existing = set(current_servers) if isinstance(current_servers, list) else set()
        for server in patch["mcpServers"]:
            if server not in existing:
                reasons.append(f"mcpServers adds {server}")
    return reasons


def enforce_mcp_allowlist(servers: list[str]) -> None:
    """Refuse MCP servers outside the configured allowlist, when one is set."""
    raw = os.environ.get("OPENMAUSBOT_MCP_ALLOWLIST")
    if raw is None:
        return
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
