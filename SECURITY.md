# Security

## Permission boundary

This server does not start its own `cua-driver` daemon and does not bypass
OpenMausBot permissions. It connects to the live embedded daemon recorded by
OpenMausBot and inherits that daemon's permission mode.

`openmausbot_call_cua_tool` is intentionally powerful. Depending on the selected
cua-driver tool, it can control applications or change local state. MCP clients
should keep their normal approval flow enabled.

Administration reads use OpenMausBot's loopback API by default. Plain HTTP is
accepted only for `127.0.0.1`, `localhost`, or `::1`; non-loopback origins must
use HTTPS. The MCP server connects to that API but does not listen on a TCP
port. Port discovery is unauthenticated and never sends a session token, and a
token is only sent to an origin configured explicitly with `OPENMAUSBOT_URL` or
`OMB_PORT`, never to a port found by probing.

OpenMausBot requires a paired-device session token for every mutation. The
team export route is a POST route and follows that requirement even though it
only returns an export. Pair through the OpenMausBot app and provide the token
directly or through an explicitly configured macOS Keychain service. Tokens
are never logged, printed, or included in tool results.

Webhook secrets, token-like fields, webhook delivery URLs containing
`/hooks/`, and provider account emails are removed or redacted before admin
results are returned.

## Reporting a vulnerability

Open a private GitHub security advisory in this repository. Do not include API
keys, access tokens, private file contents, or personal information in reports.
