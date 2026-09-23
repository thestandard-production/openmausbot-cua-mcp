from __future__ import annotations

import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED_TOOLS = {
    "openmausbot_status",
    "openmausbot_list_cua_tools",
    "openmausbot_describe_cua_tool",
    "openmausbot_call_cua_tool",
    "openmausbot_get_companion_settings",
    "openmausbot_set_companion_setting",
    "openmausbot_cua_skills_status",
    "openmausbot_cua_permissions_status",
    "openmausbot_cua_check_update",
    "openmausbot_api_health",
    "openmausbot_list_bots",
    "openmausbot_list_routines",
    "openmausbot_list_webhooks",
    "openmausbot_usage",
    "openmausbot_list_models",
    "openmausbot_list_decisions",
    "openmausbot_export_team",
}

READ_ONLY_ADMIN_TOOLS = {
    "openmausbot_api_health",
    "openmausbot_list_bots",
    "openmausbot_list_routines",
    "openmausbot_list_webhooks",
    "openmausbot_usage",
    "openmausbot_list_models",
    "openmausbot_list_decisions",
    "openmausbot_export_team",
}


def test_stdio_handshake_and_tool_catalog() -> None:
    async def check() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "openmausbot_cua_mcp"],
            env=None,
        )
        async with (
            stdio_client(parameters) as (read, write),
            ClientSession(read, write) as session,
        ):
            initialized = await session.initialize()
            tools = await session.list_tools()

        assert initialized.serverInfo.name == "openmausbot_cua_mcp"
        assert {tool.name for tool in tools.tools} == EXPECTED_TOOLS
        generic = next(tool for tool in tools.tools if tool.name == "openmausbot_call_cua_tool")
        assert generic.annotations.destructiveHint is True
        assert generic.annotations.readOnlyHint is False
        admin_tools = [tool for tool in tools.tools if tool.name in READ_ONLY_ADMIN_TOOLS]
        assert len(admin_tools) == len(READ_ONLY_ADMIN_TOOLS)
        assert all(tool.annotations.readOnlyHint is True for tool in admin_tools)
        assert all(tool.annotations.destructiveHint is False for tool in admin_tools)
        assert all(tool.annotations.idempotentHint is True for tool in admin_tools)
        assert all(tool.annotations.openWorldHint is False for tool in admin_tools)

    asyncio.run(check())
