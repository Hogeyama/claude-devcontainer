#!/bin/sh
# The isolated editor's server (code-server/README.md): `code serve-web` on
# the agent's loopback, which code-server-proxy alone reaches, through the
# firewall's DNAT (init-firewall.sh). A new connection token at each start;
# scripts/code-server.sh reads it for the editor's window.
set -eu
commit=$(cat /opt/vscode/commit)
token=/run/claude-devcontainer/vscode-token
data=/home/node/.vscode-server
cli=/home/node/.vscode/cli
(umask 077 && od -An -N16 -tx1 /dev/urandom | tr -d ' \n' >"$token")
# The image's extensions (codeServer.extensions) and Machine settings, in
# place of whatever was installed or changed in the container.
rm -rf "$data/extensions"
mkdir -p "$data/data/Machine" "$cli/serve-web"
cp -R /opt/vscode/extensions "$data/extensions"
cp /opt/vscode/machine-settings.json "$data/data/Machine/settings.json"
# serve-web starts the server of --commit-id that it finds here, and
# downloads it only when it finds none.
ln -sfn "/opt/vscode/serve-web/$commit" "$cli/serve-web/$commit"
exec /opt/vscode/bin/code serve-web --cli-data-dir "$cli" --host 127.0.0.1 --port 18000 \
    --commit-id "$commit" --connection-token-file "$token" --accept-server-license-terms \
    --server-data-dir "$data" --disable-telemetry
