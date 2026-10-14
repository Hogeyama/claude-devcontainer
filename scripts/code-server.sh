#!/bin/sh
# Run on the trusted WSL host. Never fall back to an unrestricted browser.
set -eu
scripts=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
case "${1:-open}" in
prepare|open|remove-network) action=${1:-open} ;;
*) echo "usage: $0 [prepare|open|remove-network]" >&2; exit 2 ;;
esac
python3 - "$scripts" "$action" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import settings
if sys.argv[2] != 'remove-network' and not settings.load()['codeServer']['enabled']:
    sys.exit('error: set [codeServer] enabled = true in .devcontainer/claude.local.toml and rebuild first')
PY
if ! command -v wslpath >/dev/null || ! command -v powershell.exe >/dev/null; then
    echo 'error: this launcher currently requires Windows WebView2 + WSL.' >&2
    exit 1
fi
windows=$(wslpath -w "$scripts/../code-server/windows")
case "$action" in
prepare)
    exec powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$windows\\prepare.ps1" -ConfigureNetwork ;;
open)
    # Verify the actual backend before opening the isolated client.
    "$scripts/compose.sh" exec -T agent sh -ec \
      'test "$CLAUDE_DEVCONTAINER_CODE_SERVER" = 1 && curl --fail --silent --noproxy "*" http://127.0.0.1:8088/healthz >/dev/null'
    "$scripts/compose.sh" up -d code-server-proxy
    python3 - <<'PYTHON'
import http.client
import sys
import time
for attempt in range(60):
    conn = http.client.HTTPConnection('127.0.0.1', 18086, timeout=1)
    try:
        conn.request('GET', 'http://ide.localhost:8000/healthz')
        if conn.getresponse().status == 200:
            break
    except (OSError, http.client.HTTPException):
        pass
    finally:
        conn.close()
    time.sleep(0.5)
else:
    sys.exit('error: code-server is not reachable through its browser proxy; see compose.sh logs code-server-proxy')
PYTHON
    exec powershell.exe -NoProfile -ExecutionPolicy Bypass -Command \
      '& "$env:LOCALAPPDATA\ClaudeCodeServerSetup\launch.ps1"' ;;
remove-network)
    exec powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$windows\\remove-network.ps1" ;;
esac
