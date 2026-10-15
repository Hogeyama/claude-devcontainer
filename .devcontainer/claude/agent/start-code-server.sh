#!/bin/sh
# The proxy alone reaches this loopback listener through the agent's firewall.
# scripts/code-server.sh reads the fresh token for the editor's window.
set -eu
token=/run/claude-devcontainer/vscode-token
data=/home/node/.vscode-server
(umask 077 && od -An -N32 -tx1 /dev/urandom | tr -d ' \n' >"$token")
# Restore the image's extensions and Machine settings on every start.
rm -rf "$data/extensions"
mkdir -p "$data/data/Machine"
cp -R /opt/code-server/extensions "$data/extensions"
cp /opt/code-server/machine-settings.json "$data/data/Machine/settings.json"
# code-server accepts a SHA256 hex digest as the session cookie's value.
HASHED_PASSWORD=$(cat "$token") exec /opt/code-server/bin/code-server \
    --auth password --bind-addr 127.0.0.1:18000 --disable-workspace-trust \
    --disable-telemetry --disable-update-check --disable-getting-started-override \
    --user-data-dir "$data/data" --extensions-dir "$data/extensions" /workspace
