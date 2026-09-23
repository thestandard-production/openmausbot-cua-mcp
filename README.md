# OpenMausBot CUA MCP

A local [Model Context Protocol](https://modelcontextprotocol.io/) server that
connects Claude, MiniMax, Codex, and other MCP clients to the `cua-driver`
bundled with OpenMausBot.

The server discovers OpenMausBot's current embedded socket on every call. It
does not install a background daemon, open a TCP port, or bypass OpenMausBot's
permission mode.

## Requirements

- macOS
- OpenMausBot running with Computer enabled
- Python 3.11–3.13
- [`uv`](https://docs.astral.sh/uv/) recommended for `uvx`

## Install in Claude Desktop

Add this server to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "openmausbot-cua": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/thestandard-production/openmausbot-cua-mcp.git",
        "openmausbot-cua-mcp"
      ]
    }
  }
}
```

Restart Claude Desktop after saving the configuration.

## Install in Claude Code

Put the same `mcpServers` block in your project `.mcp.json`, or run:

```bash
claude mcp add --transport stdio openmausbot-cua -- \
  uvx --from git+https://github.com/thestandard-production/openmausbot-cua-mcp.git \
  openmausbot-cua-mcp
```

## Install in MiniMax

Merge [`examples/minimax.json`](examples/minimax.json) into your MiniMax MCP
configuration, then start a new session.

## Install in Codex

Copy [`examples/codex.toml`](examples/codex.toml) into your Codex configuration.

## Tools

| Tool | Effect |
| --- | --- |
| `openmausbot_status` | Resolve the live connection and report daemon status |
| `openmausbot_list_cua_tools` | List tools exposed by the installed cua-driver |
| `openmausbot_describe_cua_tool` | Show one tool's description and input schema |
| `openmausbot_call_cua_tool` | Invoke a cua-driver tool; may control apps or modify state |
| `openmausbot_get_companion_settings` | Read companion settings |
| `openmausbot_set_companion_setting` | Atomically update one existing companion setting |
| `openmausbot_cua_skills_status` | Check agent skill-pack installation |
| `openmausbot_cua_permissions_status` | Check Accessibility and Screen Recording status |
| `openmausbot_cua_check_update` | Check for cua-driver updates without installing |

Typical sequence:

1. Call `openmausbot_status`.
2. Call `openmausbot_list_cua_tools`.
3. Call `openmausbot_describe_cua_tool` for the chosen tool.
4. Call `openmausbot_call_cua_tool` with an `arguments` object matching that schema.

## Configuration

The defaults work with a normal OpenMausBot installation. These environment
variables are available for custom installations and testing:

| Variable | Purpose |
| --- | --- |
| `OPENMAUSBOT_DATA_DIR` | Override the OpenMausBot data directory |
| `OPENMAUSBOT_CUA_CONNECTION_FILE` | Override `cua-connection.json` |
| `OPENMAUSBOT_CUA_DRIVER` | Override the `cua-driver` executable |
| `OPENMAUSBOT_CUA_SOCKET` | Override the live Unix socket |
| `OPENMAUSBOT_COMPANION_SETTINGS_FILE` | Override companion settings JSON |
| `OPENMAUSBOT_BUNDLE_ID` | Override the OpenMausBot bundle identifier |
| `OPENMAUSBOT_CUA_MAX_OUTPUT_CHARS` | Bound stdout and stderr returned to the client |

## Security

The generic call tool can perform actions on the local computer. Its MCP
annotations mark it as state-changing and potentially destructive. Keep your MCP
client's approval controls enabled and review requested tool calls.

The server only forwards `CUA_DRIVER_*` keys from OpenMausBot's connection file.
It never reads or stores API keys.

See [SECURITY.md](SECURITY.md) for the permission boundary and reporting process.

## Development

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/ruff check .
.venv/bin/pytest -q
.venv/bin/python -m build
```

## Scope

The initial release targets macOS because OpenMausBot currently exposes its
embedded connection through the macOS application data directory. Contributions
for verified installations on other platforms are welcome.

OpenMausBot and cua-driver are separate third-party projects. This repository is
an independent integration maintained by Production Craft.
