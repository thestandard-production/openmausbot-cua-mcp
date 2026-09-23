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

    asyncio.run(check())
