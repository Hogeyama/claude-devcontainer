#!/bin/sh
# Run on the trusted host: WSL for the Windows client, a Linux Wayland desktop
# for the Linux one. Never fall back to an unrestricted browser or VS Code.
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
# WSL opens the Windows client; any other Linux the sandboxed VS Code.
if command -v wslpath >/dev/null && command -v powershell.exe >/dev/null; then
    client=windows
    windows=$(wslpath -w "$scripts/../code-server/windows")
else
    client=linux
    linux=$scripts/../code-server/linux
fi
case "$action" in
prepare)
    case $client in
    windows) exec powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$windows\\prepare.ps1" -ConfigureNetwork ;;
    linux) exec python3 "$linux/client.py" --check ;;
    esac ;;
open)
    # Verify the actual backend before opening the isolated client.
    "$scripts/compose.sh" exec -T agent sh -ec \
      'test "$CLAUDE_DEVCONTAINER_CODE_SERVER" = 1 && curl --fail --silent --noproxy "*" http://127.0.0.1:8088/healthz >/dev/null'
    "$scripts/compose.sh" up -d code-server-proxy
    # The published port: compose takes a free one of its range per workspace.
    port=$("$scripts/compose.sh" port code-server-proxy 8080 | sed 's/.*://')
    case $port in
    '' | *[!0-9]*) echo 'error: code-server-proxy has no published port; see compose.sh ps' >&2; exit 1 ;;
    esac
    python3 - "$port" <<'PYTHON'
import http.client
import sys
import time
port = int(sys.argv[1])
for attempt in range(60):
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=1)
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
    case $client in
    windows)
        # The AppContainer's WFP rules name this port and no other.
        if [ "$port" != 18086 ]; then
            echo "error: the proxy is on port $port, but the Windows client is allowed 18086 only; stop the other workspace's code-server-proxy first." >&2
            exit 1
        fi
        exec powershell.exe -NoProfile -ExecutionPolicy Bypass -Command \
          '& "$env:LOCALAPPDATA\ClaudeCodeServerSetup\launch.ps1"' ;;
    linux)
        # The lock, then the agent's server (linux/server.py), then the window.
        exec python3 "$linux/client.py" --proxy-port "$port" --detach ;;
    esac ;;
remove-network)
    case $client in
    windows) exec powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$windows\\remove-network.ps1" ;;
    linux) echo 'nothing to remove: the Linux client keeps no network rules outside its sandbox.' ;;
    esac ;;
esac
