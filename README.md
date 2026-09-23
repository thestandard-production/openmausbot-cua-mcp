# OpenMausBot CUA MCP

A local [Model Context Protocol](https://modelcontextprotocol.io/) server that
connects Claude, MiniMax, Codex, and other MCP clients to the `cua-driver`
bundled with OpenMausBot.

The server discovers OpenMausBot's current embedded socket on every CUA call.
It never listens on a TCP port or installs a background daemon. Admin tools
connect outward to OpenMausBot's loopback API, and all tools preserve
OpenMausBot's permission model.

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

## Admin API (read-only)

The server also exposes bounded administration views from OpenMausBot's local
API. Sensitive webhook values and provider account emails are removed before
results reach the MCP client.

| Tool | Result |
| --- | --- |
| `openmausbot_api_health` | API health, installed app version, and compatibility findings |
| `openmausbot_list_bots` | Bots, omitting soul text by default |
| `openmausbot_list_routines` | Routines and runs, optionally within a millisecond range |
| `openmausbot_list_webhooks` | Webhooks and attempts with sensitive values redacted |
| `openmausbot_usage` | Usage and billing summaries for a date range of up to one year |
| `openmausbot_list_models` | Provider instances, model options, and effort levels |
| `openmausbot_list_decisions` | Recent decisions, with a limit from 1 to 500 |
| `openmausbot_export_team` | Team export in manifest, package, or backup format |

`openmausbot_export_team` calls OpenMausBot's export endpoint, which is a POST
route and therefore requires a paired-device session token. Pairing is always
completed through the OpenMausBot app; this package does not create sessions.

The same views are available from `omb-ctl`. JSON is the default output. Bots
and routines also support a compact text table.

```bash
omb-ctl status
omb-ctl bots --text
omb-ctl bots --include-soul
omb-ctl routines --since-hours 24 --text
omb-ctl webhooks
omb-ctl usage --from 2026-01-01 --to 2026-01-31 --group-by model
omb-ctl models
omb-ctl decisions --limit 100
omb-ctl export --format manifest
```

The Admin API client checks `127.0.0.1` ports 8799, 18799, and 28799 when no
origin or port override is configured. The following environment variables
control this client:

| Variable | Purpose |
| --- | --- |
| `OPENMAUSBOT_URL` | API origin override; HTTP is restricted to loopback hosts |
| `OMB_PORT` | Loopback API port override used when no URL override is set |
| `OPENMAUSBOT_TOKEN` | Paired-device session token |
| `OPENMAUSBOT_TOKEN_KEYCHAIN_SERVICE` | macOS Keychain service containing the token |
| `OPENMAUSBOT_TOKEN_KEYCHAIN_ACCOUNT` | Optional Keychain account used with the service |
| `OPENMAUSBOT_API_TIMEOUT` | API request timeout in seconds; default 10 |
| `OPENMAUSBOT_APP_PLIST` | App `Info.plist` override for version detection |
| `OPENMAUSBOT_ENABLE_ADMIN_WRITES` | Set to `1` to enable MCP administration write tools |
| `OPENMAUSBOT_MCP_ALLOWLIST` | Optional comma-separated MCP server names allowed on bots |

Port discovery never sends the token, and a token is only ever sent to an origin
you named explicitly with `OPENMAUSBOT_URL` or `OMB_PORT` (the same rule as
OpenMausBot's own MCP client): a port found by probing could belong to another
local process. Reads against a discovered port work without the token; requests
that need it fail with a message asking you to set the origin. No request sends
a browser `Origin` header. Server error messages are passed through (bounded).

## Admin API (writes)

Administration writes use two independent gates:

- Every mutation requires a paired-device session token and an explicit API origin through
  `OPENMAUSBOT_URL` or `OMB_PORT`.
- MCP write tools are disabled unless `OPENMAUSBOT_ENABLE_ADMIN_WRITES=1`. The `omb-ctl` CLI does
  not use that environment gate; it requires `--apply` for each mutation.

All write tools and CLI commands are dry runs by default. An applied PATCH or POST is followed by a
fresh GET and field-by-field verification. If the read-back does not match, the result has
`"ok": false`, `"status": "unknown"`, and lists the mismatched fields. Routine creation is
idempotent by exact routine name because the public create endpoint has no idempotency key;
duplicate names must be resolved in the app first.

MCP write tools include guarded bot updates and model selection, routine upsert/enable/run/delete,
and run cancellation. `openmausbot_plan` is read-only and plans a desired-state file; applying a
desired-state item is intentionally CLI-only.

```bash
# Dry run (no mutation)
omb-ctl bot-update research-bot --json '{"title":"Research"}'
omb-ctl routine-enable daily-summary

# Apply and verify
omb-ctl bot-update research-bot --json '{"title":"Research"}' --apply
omb-ctl routine-enable daily-summary --apply
```

Desired-state files are JSON, with YAML available through the optional `yaml` extra. Referenced
files must be relative to the desired file and stay within its directory. Only fields present in a
bot entry are managed.

```json
{
  "version": 1,
  "bots": [
    {
      "name": "research-bot",
      "title": "Research",
      "soul_file": "souls/research.md",
      "soul_append_files": ["souls/common.md"],
      "computer": "off",
      "mcpServers": [],
      "allow_loosen": false
    }
  ],
  "routines": [
    {
      "name": "daily-summary",
      "bot": "research-bot",
      "prompt_file": "prompts/daily-summary.md",
      "schedule": {"type": "daily", "time": "09:00", "weekdays": [1, 2, 3, 4, 5]},
      "enabled": true,
      "runOn": "maus",
      "durationMinutes": 30,
      "overlap": "skip"
    }
  ]
}
```

Routine schedules are validated against, and normalized to, the shape OpenMausBot stores, so an
unchanged desired state plans as a no-op and a read-back compares equal:

| `type` | Fields | Notes |
| --- | --- | --- |
| `once` | `at` | Epoch milliseconds or RFC3339 with an offset (converted to epoch ms) |
| `daily` | `time`, `weekdays` | `HH:MM`; weekdays as `0`-`6` (Sunday = 0) or names; omitted means every day |
| `interval` | `everyMinutes`, `anchorAt`, optional `weekdays`, `window`, `endsAt` | 5-1440 minutes; `anchorAt` (first run) is required; all seven weekdays is stored as no restriction |
| `cron` | `expression`, `timeZone` | Five fields; an IANA `timeZone` such as `Asia/Bangkok` or `UTC` is required |

Routine `name` and `prompt` are trimmed the same way the app trims them.

Plan the entire file, then select exactly one item to apply:

```bash
omb-ctl plan examples/desired.example.json
omb-ctl apply examples/desired.example.json --only bot:research-bot
omb-ctl apply examples/desired.example.json --only bot:research-bot --apply
```

CLI exit codes are `0` for success or no change, `10` when a plan or dry run contains a change, and
`2` for an error, unknown write status, or verification mismatch.

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
