"""MCP tools for OpenMausBot's bundled cua-driver."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .api import ApiClient, OpenMausBotApiError
from .bridge import (
    OpenMausBotConnectionError,
    read_companion_settings,
    resolve_connection,
    run_cua,
    write_companion_setting,
)

mcp = FastMCP("openmausbot_cua_mcp")

ToolName = Annotated[
    str,
    Field(
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Exact cua-driver tool name returned by openmausbot_list_cua_tools.",
    ),
]


def _error(exc: Exception) -> dict[str, Any]:
    return {"ok": False, "error": str(exc)}


@mcp.tool(
    name="openmausbot_status",
    annotations={
        "title": "Check OpenMausBot CUA status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def openmausbot_status() -> dict[str, Any]:
    """Check discovery and the live cua-driver daemon without changing state."""
    try:
        connection = resolve_connection()
        result = run_cua(["status"])
        response = result.as_dict()
        response.update(
            {
                "driver": str(connection.command),
                "connection_file": str(connection.connection_file),
                "bundle_id": connection.bundle_id,
            }
        )
        return response
    except OpenMausBotConnectionError as exc:
        return _error(exc)


@mcp.tool(
    name="openmausbot_list_cua_tools",
    annotations={
        "title": "List OpenMausBot CUA tools",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def openmausbot_list_cua_tools() -> dict[str, Any]:
    """List every tool exposed by the cua-driver bundled with OpenMausBot."""
    try:
        return run_cua(["list-tools"]).as_dict()
    except OpenMausBotConnectionError as exc:
        return _error(exc)


@mcp.tool(
    name="openmausbot_describe_cua_tool",
    annotations={
        "title": "Describe an OpenMausBot CUA tool",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def openmausbot_describe_cua_tool(name: ToolName) -> dict[str, Any]:
    """Return the description and JSON input schema for one cua-driver tool."""
    try:
        return run_cua(["describe", name]).as_dict()
    except OpenMausBotConnectionError as exc:
        return _error(exc)


@mcp.tool(
    name="openmausbot_call_cua_tool",
    annotations={
        "title": "Call an OpenMausBot CUA tool",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
def openmausbot_call_cua_tool(
    name: ToolName,
    arguments: Annotated[
        dict[str, Any] | None,
        Field(description="JSON object matching the schema returned by describe_cua_tool."),
    ] = None,
) -> dict[str, Any]:
    """Invoke one cua-driver tool; some tools can control apps or modify local state.

    The connected OpenMausBot daemon applies its own permission mode. Call
    openmausbot_describe_cua_tool first when the input schema is not known.
    """
    try:
        payload = json.dumps(arguments or {}, separators=(",", ":"))
        return run_cua(["call", name, payload]).as_dict()
    except OpenMausBotConnectionError as exc:
        return _error(exc)


@mcp.tool(
    name="openmausbot_get_companion_settings",
    annotations={
        "title": "Read OpenMausBot companion settings",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def openmausbot_get_companion_settings() -> dict[str, Any]:
    """Read OpenMausBot's companion settings JSON file."""
    try:
        return {"ok": True, "settings": read_companion_settings()}
    except OpenMausBotConnectionError as exc:
        return _error(exc)


@mcp.tool(
    name="openmausbot_set_companion_setting",
    annotations={
        "title": "Update one OpenMausBot companion setting",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def openmausbot_set_companion_setting(
    key: Annotated[
        str,
        Field(min_length=1, max_length=120, description="An existing companion setting key."),
    ],
    value: Annotated[Any, Field(description="New JSON-compatible value for the setting.")],
) -> dict[str, Any]:
    """Atomically update one existing key in OpenMausBot's companion settings."""
    try:
        return write_companion_setting(key, value)
    except (OpenMausBotConnectionError, OSError) as exc:
        return _error(exc)


def _read_only_command(arguments: list[str]) -> dict[str, Any]:
    try:
        return run_cua(arguments).as_dict()
    except OpenMausBotConnectionError as exc:
        return _error(exc)


@mcp.tool(
    name="openmausbot_cua_skills_status",
    annotations={
        "title": "Check CUA skill installation",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def openmausbot_cua_skills_status() -> dict[str, Any]:
    """Report cua-driver skill pack installation status for supported agents."""
    return _read_only_command(["skills", "status"])


@mcp.tool(
    name="openmausbot_cua_permissions_status",
    annotations={
        "title": "Check CUA macOS permissions",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def openmausbot_cua_permissions_status() -> dict[str, Any]:
    """Report Accessibility and Screen Recording permission status."""
    return _read_only_command(["permissions", "status"])


@mcp.tool(
    name="openmausbot_cua_check_update",
    annotations={
        "title": "Check for a cua-driver update",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
def openmausbot_cua_check_update() -> dict[str, Any]:
    """Check the configured cua-driver release channel without installing anything."""
    return _read_only_command(["check-update"])


@mcp.tool(
    name="openmausbot_api_health",
    annotations={
        "title": "Check OpenMausBot Admin API health",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def openmausbot_api_health() -> dict[str, Any]:
    """Return local API health, app version, and compatibility findings."""
    try:
        client = ApiClient()
        health = client.health()
        compatibility = client.check_compatibility()
        return {
            "ok": True,
            "health": health,
            "version": compatibility["version"],
            "compatibility": compatibility,
        }
    except OpenMausBotApiError as exc:
        return _error(exc)


@mcp.tool(
    name="openmausbot_list_bots",
    annotations={
        "title": "List OpenMausBot bots",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def openmausbot_list_bots(include_soul: bool = False) -> dict[str, Any]:
    """List bots without long soul text unless explicitly requested."""
    try:
        return {"ok": True, **ApiClient().list_bots(include_soul=include_soul)}
    except OpenMausBotApiError as exc:
        return _error(exc)


@mcp.tool(
    name="openmausbot_list_routines",
    annotations={
        "title": "List OpenMausBot routines",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def openmausbot_list_routines(
    from_ms: Annotated[
        int | None, Field(ge=0, description="Optional range start in Unix ms.")
    ] = None,
    to_ms: Annotated[int | None, Field(ge=0, description="Optional range end in Unix ms.")] = None,
) -> dict[str, Any]:
    """List routines and runs, optionally within a millisecond range."""
    try:
        return {"ok": True, **ApiClient().list_routines(from_ms=from_ms, to_ms=to_ms)}
    except OpenMausBotApiError as exc:
        return _error(exc)


@mcp.tool(
    name="openmausbot_list_webhooks",
    annotations={
        "title": "List redacted OpenMausBot webhooks",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def openmausbot_list_webhooks() -> dict[str, Any]:
    """List webhooks and attempts with secret material redacted."""
    try:
        return {"ok": True, **ApiClient().list_webhooks()}
    except OpenMausBotApiError as exc:
        return _error(exc)


@mcp.tool(
    name="openmausbot_usage",
    annotations={
        "title": "Read OpenMausBot usage",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def openmausbot_usage(
    from_date: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="YYYY-MM-DD")],
    to_date: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="YYYY-MM-DD")],
    group_by: Annotated[
        str | None,
        Field(min_length=1, max_length=120, description="Optional API grouping dimension."),
    ] = None,
) -> dict[str, Any]:
    """Read usage for a date range of no more than one year."""
    try:
        return {
            "ok": True,
            **ApiClient().usage(from_date=from_date, to_date=to_date, group_by=group_by),
        }
    except OpenMausBotApiError as exc:
        return _error(exc)


@mcp.tool(
    name="openmausbot_list_models",
    annotations={
        "title": "List OpenMausBot models",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def openmausbot_list_models() -> dict[str, Any]:
    """List provider instances and models without account details."""
    try:
        return {"ok": True, **ApiClient().list_models()}
    except OpenMausBotApiError as exc:
        return _error(exc)


@mcp.tool(
    name="openmausbot_list_decisions",
    annotations={
        "title": "List OpenMausBot decisions",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def openmausbot_list_decisions(
    limit: Annotated[int, Field(ge=1, le=500, description="Number of decisions to return.")] = 50,
) -> dict[str, Any]:
    """List recent decisions."""
    try:
        return {"ok": True, **ApiClient().decisions(limit=limit)}
    except OpenMausBotApiError as exc:
        return _error(exc)


@mcp.tool(
    name="openmausbot_export_team",
    annotations={
        "title": "Export OpenMausBot team data",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def openmausbot_export_team(
    format: Annotated[
        Literal["manifest", "package", "backup"],
        Field(description="Team export format."),
    ] = "manifest",
) -> dict[str, Any]:
    """Export team data using a paired-device session token."""
    try:
        return {"ok": True, **ApiClient().export_team(format=format)}
    except OpenMausBotApiError as exc:
        return _error(exc)


def main() -> None:
    """Run the local MCP server over stdio."""
    mcp.run(transport="stdio")
