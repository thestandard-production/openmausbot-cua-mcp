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
    "openmausbot_plan",
    "openmausbot_update_bot",
    "openmausbot_set_bot_model",
    "openmausbot_upsert_routine",
    "openmausbot_set_routine_enabled",
    "openmausbot_run_routine_now",
    "openmausbot_delete_routine",
    "openmausbot_cancel_run",
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
    "openmausbot_plan",
}

WRITE_ADMIN_TOOLS = {
    "openmausbot_update_bot",
    "openmausbot_set_bot_model",
    "openmausbot_upsert_routine",
    "openmausbot_set_routine_enabled",
    "openmausbot_run_routine_now",
    "openmausbot_delete_routine",
    "openmausbot_cancel_run",
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
        write_tools = [tool for tool in tools.tools if tool.name in WRITE_ADMIN_TOOLS]
        assert len(write_tools) == len(WRITE_ADMIN_TOOLS)
        assert all(tool.annotations.readOnlyHint is False for tool in write_tools)
        assert all(tool.annotations.openWorldHint is False for tool in write_tools)
        destructive = {
            tool.name for tool in write_tools if tool.annotations.destructiveHint is True
        }
        assert destructive == {"openmausbot_delete_routine", "openmausbot_cancel_run"}

    asyncio.run(check())


def test_mcp_write_tools_are_disabled_without_environment(monkeypatch) -> None:
    from openmausbot_cua_mcp import server

    monkeypatch.delenv("OPENMAUSBOT_ENABLE_ADMIN_WRITES", raising=False)

    def fail():
        raise AssertionError("ApiClient must not be constructed while writes are disabled")

    monkeypatch.setattr(server, "ApiClient", fail)
    result = server.openmausbot_update_bot("bot-1", {"title": "New"})
    assert result == {
        "ok": False,
        "error": (
            "admin writes are disabled; set OPENMAUSBOT_ENABLE_ADMIN_WRITES=1 and provide a "
            "paired session token"
        ),
    }
