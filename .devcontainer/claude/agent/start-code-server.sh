#!/bin/sh
set -eu
export VSCODE_PROXY_URI='http://port-{{port}}.localhost:8000'
exec /opt/code-server/bin/code-server --bind-addr 0.0.0.0:8088 --auth none \
    --disable-telemetry --disable-update-check --proxy-domain 'port-{{port}}.localhost:8000' \
    --user-data-dir /home/node/.local/share/code-server/user-data \
    --extensions-dir /home/node/.local/share/code-server/extensions /workspace
