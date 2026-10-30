#!/bin/sh
# Run on the trusted host: in WSL for the Windows client, on a Linux Wayland or X11
# desktop for the Linux one. Both open the editor's Electron shell
# (code-server/shell/), never an unrestricted browser or VS Code.
set -eu
scripts=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
case "${1-}" in
'') action=open ;;
prepare) action=prepare ;;
*) echo "usage: $0 [prepare]" >&2; exit 2 ;;
esac
python3 - "$scripts" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import settings
if not settings.load()['codeServer']['enabled']:
    if settings.layout.host_mode():
        sys.exit(f'error: set [codeServer] enabled = true in {settings.CONFIG / "claude.toml"}, '
                 'then run claude-devcontainer down and claude-devcontainer up')
    sys.exit('error: set [codeServer] enabled = true in .devcontainer/claude.local.toml and rebuild first')
PY
# WSL opens the shell on Windows; any other Linux, in a Wayland or X11 session on Linux.
if command -v wslpath >/dev/null && command -v powershell.exe >/dev/null; then
    client=$scripts/../code-server/windows/client.py
    set --
else
    client=$scripts/../code-server/linux/client.py
    set -- --detach
fi
case "$action" in
prepare)
    # Downloads Electron; no administrator rights on either.
    exec python3 "$client" --prepare ;;
open)
    . "$scripts/compose-context.sh"
    prepare_compose up
    # Verify the actual backend before opening the window.
    # A setting turned on after the agent was created reaches it only when
    # the agent is created again.
    if [ -n "${CLAUDE_DEVCONTAINER_CONFIG_DIR-}" ]; then
        start='claude-devcontainer up'
        recreate='claude-devcontainer down, then claude-devcontainer up'
    else
        start='devcontainer up'
        recreate='devcontainer up --remove-existing-container'
    fi
    if ! enabled=$(compose exec -T agent printenv CLAUDE_DEVCONTAINER_CODE_SERVER); then
        echo "error: the agent is not running; start it with $start" >&2
        exit 1
    fi
    if [ "$enabled" != 1 ]; then
        echo "error: the agent was created without codeServer.enabled; create it again with $recreate" >&2
        exit 1
    fi
    compose up -d code-server-proxy
    # The published port: compose takes a free one of its range per workspace.
    port=$(compose port code-server-proxy 8080 | sed 's/.*://')
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
        conn.request('GET', 'http://localhost:18000/healthz')
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
    CLAUDE_DEVCONTAINER_VSCODE_TOKEN=$(compose exec -T agent cat /run/claude-devcontainer/vscode-token)
    export CLAUDE_DEVCONTAINER_VSCODE_TOKEN
    exec python3 "$client" --proxy-port "$port" "$@" ;;
esac
