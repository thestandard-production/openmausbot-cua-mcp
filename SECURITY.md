# Security

## Permission boundary

This server does not start its own `cua-driver` daemon and does not bypass
OpenMausBot permissions. It connects to the live embedded daemon recorded by
OpenMausBot and inherits that daemon's permission mode.

`openmausbot_call_cua_tool` is intentionally powerful. Depending on the selected
cua-driver tool, it can control applications or change local state. MCP clients
should keep their normal approval flow enabled.

## Reporting a vulnerability

Open a private GitHub security advisory in this repository. Do not include API
keys, access tokens, private file contents, or personal information in reports.
