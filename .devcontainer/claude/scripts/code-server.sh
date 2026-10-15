#!/bin/sh
# Run on the trusted host: in WSL for the Windows client, on a Linux Wayland
# desktop for the Linux one. Both open the editor's Electron shell
# (code-server/shell/), never an unrestricted browser or VS Code.
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
# WSL opens the shell on Windows; any other Linux, in a sandbox on Linux.
if command -v wslpath >/dev/null && command -v powershell.exe >/dev/null; then
    client=$scripts/../code-server/windows/client.py
    windows=1
else
    client=$scripts/../code-server/linux/client.py
    windows=0
fi
case "$action" in
prepare)
    # Downloads Electron; no administrator rights on either.
    exec python3 "$client" --prepare ;;
open)
    # Verify the actual backend before opening the window.
    "$scripts/compose.sh" exec -T agent sh -ec \
      'test "$CLAUDE_DEVCONTAINER_CODE_SERVER" = 1 && curl --fail --silent --noproxy "*" http://127.0.0.1:18000/version >/dev/null'
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
        conn.request('GET', 'http://localhost:18000/version')
        if conn.getresponse().status == 200:
            break
    except (OSError, http.client.HTTPException):
        pass
    finally:
        conn.close()
    time.sleep(0.5)
else:
    sys.exit("error: the editor's server is not reachable through code-server-proxy; see compose.sh logs code-server-proxy")
PYTHON
    # The server's token, new at each start of the agent, for the window alone.
    CLAUDE_DEVCONTAINER_VSCODE_TOKEN=$("$scripts/compose.sh" exec -T agent cat /run/claude-devcontainer/vscode-token)
    export CLAUDE_DEVCONTAINER_VSCODE_TOKEN
    if [ "$windows" = 1 ]; then
        exec python3 "$client" --proxy-port "$port"
    fi
    # The lock, then the window, in a sandbox that stays when this returns.
    exec python3 "$client" --proxy-port "$port" --detach ;;
remove-network)
    # The former WebView2 client's WFP rules and loopback exemption.
    if [ "$windows" = 1 ]; then
        exec powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(wslpath -w "$scripts/../code-server/windows/remove-network.ps1")"
    fi
    echo 'nothing to remove: the Linux client keeps no network rules outside its sandbox.' ;;
esac
