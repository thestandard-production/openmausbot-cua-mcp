# Pairing: giving omb-ctl and the MCP server write access

OpenMausBot's local API lets anyone on the machine read (`GET` on loopback needs no key).
Every write needs the session token of a **paired device**, and that token has to come from a
person operating the app. This page shows how to do it safely, including when an AI assistant
is doing the work.

## For AI assistants

- **Reads need no token.** `openmausbot_list_bots`, `openmausbot_plan`, `omb-ctl plan` and
  `omb-ctl status` work as soon as the app is running.
- **Writes need a token that a person creates in the app.** This package does not create
  sessions, and neither should you.
- Do not look for a token yourself: do not read files inside the app bundle (for example the
  app's own pairing CLI), do not search the Keychain, and do not run `npx openmausbot pair
  --client` on the user's behalf (it downloads and runs a package; that is the user's call).
  Agent safety layers classify these as credential exploration and block them, correctly.
- Instead, ask the user to run [`scripts/omb-pair.sh`](../scripts/omb-pair.sh) (steps below).
  The code goes from the user's keyboard into the script; you never see the code or the token.
- If a command is blocked, stop and ask the user. Do not try an equivalent command.
- While you wait, anything the app UI can do is still available with the user's approval.

## Pair once (about two minutes)

1. In the app: **Settings → Remote access → Pair a phone or another computer**.
2. Choose **Full access** ("Chat and approvals only" cannot edit bots), then **Create pairing
   code**. The code looks like `XXXX-XXXX-XXXX`, works once, and expires after five minutes.
   **Copy link** also works.
3. Run the script and paste the code (input is hidden; that is normal):

   ```bash
   OMB_PAIR_LABEL="omb-ctl · my-team" scripts/omb-pair.sh
   ```

4. The script exchanges the code for a token, stores it in the macOS Keychain (service
   `OPENMAUSBOT_TOKEN`, account `omb-ctl`), reads it back, and verifies it. `OK: token works …
   scopes=admin,client` means you are done.

Check later with `scripts/omb-pair.sh --check`. Before relying on the script on a new machine you
can run `scripts/omb-pair.sh --self-test`, which exercises the whole path against a local mock
server and removes its temporary Keychain item.

## Point the tools at the token

```
OPENMAUSBOT_URL=http://127.0.0.1:8799
OPENMAUSBOT_TOKEN_KEYCHAIN_SERVICE=OPENMAUSBOT_TOKEN
OPENMAUSBOT_TOKEN_KEYCHAIN_ACCOUNT=omb-ctl
```

These name the Keychain item; the token itself is never written to a config file. MCP write tools
additionally need `OPENMAUSBOT_ENABLE_ADMIN_WRITES=1`; `omb-ctl` mutations need `--apply`. Both
default to dry runs and read back every change (see the README's Admin API section).

## Revoke or renew

- **Revoke:** app → Settings → Remote access → Paired devices → **Sign out** the session, then
  `security delete-generic-password -a omb-ctl -s OPENMAUSBOT_TOKEN`. This disables both the MCP
  server's writes and `omb-ctl` at once.
- **Renew:** a session lasts 30 days and extends itself while in use (up to 180 days). After that,
  pair again.

## What a token still cannot do

- Change the model of a bot that has more than one thread: the API answers HTTP 409 ("This bot has
  more than one thread"). Change it in the app.
- Widen access without a person: turning on computer control, connected apps or the built-in
  browser, adding an MCP server, or moving the working folder needs `allow_loosen`, and
  `approvalMode` can only be set to `ask`. Widening approvals is left to the app.
