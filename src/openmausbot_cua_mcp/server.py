"""MCP tools for OpenMausBot's bundled cua-driver."""

from __future__ import annotations

import json
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

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


def main() -> None:
    """Run the local MCP server over stdio."""
    mcp.run(transport="stdio")
