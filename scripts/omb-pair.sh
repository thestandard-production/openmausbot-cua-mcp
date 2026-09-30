#!/bin/bash
# omb-pair.sh — pair omb-ctl / the MCP server with the OpenMausBot app and keep the
# session token in the macOS Keychain.
#
# Why: every write to OpenMausBot's local API needs the session token of a paired device.
# The pairing code can only be created inside the app on this Mac (valid 5 minutes) and is
# exchanged for a token. This script has a person paste the code with echo off, exchanges it,
# stores the token in the Keychain over stdin, and reads it back. The code and the token never
# appear in a chat, in argv, or on screen.
#
# Usage:  scripts/omb-pair.sh              pair (interactive)
#         scripts/omb-pair.sh --check      verify the stored token (value is never shown)
#         scripts/omb-pair.sh --self-test  run the whole path against a local mock server
#                                          (temporary Keychain item, deleted afterwards)
# Undo:   OpenMausBot app -> Settings -> Remote access -> Paired devices -> Sign out
#         security delete-generic-password -a omb-ctl -s OPENMAUSBOT_TOKEN
#
# Then point omb-ctl / the MCP server at the Keychain item:
#   OPENMAUSBOT_URL=http://127.0.0.1:8799
#   OPENMAUSBOT_TOKEN_KEYCHAIN_SERVICE=OPENMAUSBOT_TOKEN
#   OPENMAUSBOT_TOKEN_KEYCHAIN_ACCOUNT=omb-ctl
#
# Environment overrides: OPENMAUSBOT_URL, OMB_PAIR_SERVICE, OMB_PAIR_ACCOUNT,
# OMB_PAIR_LABEL (session name shown in the app), OMB_PAIR_NOWAIT=1 (no "press Enter").
#
# Implementation note: the Python program is passed with -c and the code arrives on stdin.
# Do not switch to a heredoc for the program — it steals stdin and the code silently becomes empty.

set -u
PY=/usr/bin/python3
BASE="${OPENMAUSBOT_URL:-http://127.0.0.1:8799}"
SERVICE="${OMB_PAIR_SERVICE:-OPENMAUSBOT_TOKEN}"
ACCOUNT="${OMB_PAIR_ACCOUNT:-omb-ctl}"
LABEL="${OMB_PAIR_LABEL:-omb-ctl}"
NOWAIT="${OMB_PAIR_NOWAIT:-}"

pause() { [ -n "$NOWAIT" ] || read -r -p "Press Enter to close" _; }

read -r -d '' CHECK_PY <<'EOF'
import json, subprocess, sys, urllib.request, urllib.error, datetime as dt
base, service, account = sys.argv[1:4]
r = subprocess.run(["/usr/bin/security", "find-generic-password", "-a", account, "-s", service, "-w"],
                   capture_output=True, text=True)
token = r.stdout.strip()
if r.returncode != 0 or not token:
    print("FAIL: no token in the Keychain (service=%s account=%s)" % (service, account)); sys.exit(2)
req = urllib.request.Request(base + "/api/auth/session", headers={"Authorization": "Bearer " + token})
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        s = json.load(resp)
except urllib.error.HTTPError as e:
    print("FAIL: the app rejected the token (HTTP %d) - the session may have been signed out or expired; pair again" % e.code); sys.exit(2)
except OSError as e:
    print("FAIL: cannot reach the app at %s (%s) - start OpenMausBot first" % (base, e)); sys.exit(2)
if s.get("kind") != "session":
    print("FAIL: the app does not treat this as a paired session (kind=%r)" % s.get("kind")); sys.exit(2)
exp = s.get("expiresAt")
exp_s = dt.datetime.fromtimestamp(exp / 1000).strftime("%Y-%m-%d %H:%M") if isinstance(exp, (int, float)) else "?"
scopes = s.get("scopes") or []
print("OK: token works - session=%s label=%s scopes=%s expires %s (renews itself while in use)"
      % (s.get("id"), s.get("label"), ",".join(scopes), exp_s))
if "admin" not in scopes:
    print("WARN: this session has no admin scope - it cannot edit bots or routines. Sign it out and pair again choosing Full access."); sys.exit(10)
EOF

read -r -d '' EXCHANGE_PY <<'EOF'
import hashlib, json, re, subprocess, sys, urllib.parse, urllib.request, urllib.error
base, service, account, label = sys.argv[1:5]
raw = sys.stdin.read()
# Strip escape sequences (bracketed paste etc.). The app accepts three shapes:
# (1) a credential omb_pair_... (inside the openmausbot://...&token= link from "Copy link"; case matters)
# (2) a link ending in /pair#code=XXXX-XXXX-XXXX   (3) a typed/pasted code XXXX-XXXX-XXXX
clean = urllib.parse.unquote(re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", raw)).strip()
cred = re.search(r"omb_pair_[A-Za-z0-9_-]{20,}", clean)
m = re.search(r"([2-9A-HJ-NP-Z]{4})[-\s]?([2-9A-HJ-NP-Z]{4})[-\s]?([2-9A-HJ-NP-Z]{4})",
              clean.split("code=", 1)[-1].upper())
if cred:
    code, kind = cred.group(0), "link/credential"
elif m:
    code, kind = "-".join(m.groups()), "12-character code"
else:
    shape = "%d characters%s%s" % (len(clean), ", contains ://" if "://" in clean else "",
                                   ", starts with omb_" if clean.startswith("omb_") else "")
    print("FAIL: no pairing code or link found in what was pasted (%s) - nothing was sent. Paste the "
          "XXXX-XXXX-XXXX code, or press Copy link and paste the whole link." % shape); sys.exit(2)
print("Exchanging the %s with the app..." % kind)
req = urllib.request.Request(base + "/api/auth/pair", method="POST",
                             data=json.dumps({"code": code, "label": label}).encode(),
                             headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        payload = json.load(resp)
except urllib.error.HTTPError as e:
    try:
        msg = json.load(e).get("error", "")
    except Exception:
        msg = ""
    print("FAIL: exchange rejected (HTTP %d) %s - the code may have expired (5 minutes) or been used already; create a new one" % (e.code, msg)); sys.exit(2)
token = payload.get("token") if isinstance(payload, dict) else None
if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9._~+/=-]{16,512}", token):
    print("FAIL: the app's reply had no token in the expected shape - nothing stored"); sys.exit(2)
cmd = 'add-generic-password -U -a "%s" -s "%s" -l "OpenMausBot omb-ctl session" -w "%s"\n' % (account, service, token)
subprocess.run(["/usr/bin/security", "-i"], input=cmd, capture_output=True, text=True)
back = subprocess.run(["/usr/bin/security", "find-generic-password", "-a", account, "-s", service, "-w"],
                      capture_output=True, text=True).stdout.strip()
if hashlib.sha256(back.encode()).digest() != hashlib.sha256(token.encode()).digest():
    print("FAIL: Keychain read-back does not match - sign this session out in the app and pair again"); sys.exit(2)
s = payload.get("session") or {}
print("Stored the token in the Keychain (service=%s account=%s) session=%s" % (service, account, s.get("id")))
EOF

check_token() { "$PY" -c "$CHECK_PY" "$BASE" "$SERVICE" "$ACCOUNT"; }

self_test() {
  # Mock server on loopback: accepts only the expected code/credential, issues a token, answers /api/auth/session.
  local port pid rc=0 svc=OMB_PAIR_SELFTEST_$$ cred="omb_pair_Ab3_xY-9kLmNoPqRsTuVwXyZ0123456789abcdEF"
  port=$("$PY" -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])')
  "$PY" -c '
import http.server, json, sys
CODE, CRED, TOKEN = "K7QX-M4RT-9ZPA", sys.argv[2], "omb_sess_selftest_0123456789abcdefghijklmnop"
class H(http.server.BaseHTTPRequestHandler):
    def _j(self, code, obj):
        b = json.dumps(obj).encode(); self.send_response(code)
        self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path == "/api/health": return self._j(200, {"app": "openmausbot"})
        if self.path == "/api/auth/session":
            ok = self.headers.get("Authorization") == "Bearer " + TOKEN
            return self._j(200, {"kind": "session", "id": "s1", "label": "omb-ctl", "scopes": ["admin", "client"],
                                 "expiresAt": 1893456000000}) if ok else self._j(401, {"error": "bad token"})
        self._j(404, {})
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path == "/api/auth/pair" and body.get("code") in (CODE, CRED):
            return self._j(200, {"token": TOKEN, "session": {"id": "s1"}})
        self._j(401, {"error": "pairing code is wrong or has expired"})
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
' "$port" "$cred" &
  pid=$!; sleep 0.6
  run_case() {  # $1 case name · $2 what is "pasted" · $3 expected outcome (ok|fail)
    local out got
    out=$(printf '%s\n' "$2" | OPENMAUSBOT_URL="http://127.0.0.1:$port" OMB_PAIR_SERVICE="$svc" OMB_PAIR_NOWAIT=1 "$0" 2>&1); got=$?
    /usr/bin/security delete-generic-password -a "$ACCOUNT" -s "$svc" >/dev/null 2>&1
    if { [ "$3" = ok ] && [ $got -eq 0 ]; } || { [ "$3" = fail ] && [ $got -ne 0 ]; }; then echo "  ok   $1"
    else echo "  FAIL $1 (rc=$got)"; echo "$out" | sed 's/^/      /'; rc=1; fi
  }
  echo "self-test (mock server :$port, temporary Keychain service $svc)"
  run_case "typed code"                              "K7QX-M4RT-9ZPA" ok
  run_case "code pasted with bracketed-paste marks"  $'\e[200~K7QX-M4RT-9ZPA\e[201~' ok
  run_case "openmausbot:// link from Copy link"      "openmausbot://pair?address=http%3A%2F%2F127.0.0.1&token=${cred}&name=x" ok
  run_case "wrong code must fail"                    "ZZZZ-ZZZZ-ZZZZ" fail
  run_case "empty input must fail"                   "" fail
  kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
  [ $rc -eq 0 ] && echo "self-test OK" || echo "self-test FAILED"
  return $rc
}

case "${1:-}" in
  --check) check_token; exit $? ;;
  --self-test) self_test; exit $? ;;
esac

echo "== Pair omb-ctl with OpenMausBot =="
if ! curl -s -m 3 "$BASE/api/health" | grep -q '"app": *"openmausbot"'; then
  echo "FAIL: OpenMausBot not found at $BASE - start the app and run this again"; pause; exit 2
fi
if /usr/bin/security find-generic-password -a "$ACCOUNT" -s "$SERVICE" >/dev/null 2>&1; then
  echo "Note: a token already exists in the Keychain; this run overwrites it (the old session stays in the app - sign it out under Remote access)"
fi
cat <<'MSG'

1) In the OpenMausBot app: Settings -> Remote access -> "Pair a phone or another computer"
2) Choose "Full access" (not "Chat and approvals only") -> "Create pairing code"
3) You get a code like XXXX-XXXX-XXXX, valid once for 5 minutes
4) Paste the code - or press "Copy link" and paste the whole link - below (nothing is echoed) and press Enter

MSG
# Turn bracketed paste off before reading (the Python filter catches leftovers).
printf '\e[?2004l'
read -r -s -p "Pairing code or link: " CODE; echo
if [ -z "$CODE" ]; then echo "FAIL: no code entered"; pause; exit 2; fi

printf '%s' "$CODE" | "$PY" -c "$EXCHANGE_PY" "$BASE" "$SERVICE" "$ACCOUNT" "$LABEL"
rc=$?
unset CODE
if [ $rc -eq 0 ]; then
  echo; check_token; rc=$?
fi
echo
if [ $rc -eq 0 ]; then echo "Done - tell your assistant the device is paired (it never needs to see the token)."; else echo "Not finished (rc=$rc) - read the message above."; fi
pause
exit $rc
