#!/bin/sh
set -eu
mkdir -p "$HOME" /tmp/data/User /tmp/extensions/demo.boundary-probe-0.0.1
cp /demo/extension/* /tmp/extensions/demo.boundary-probe-0.0.1/
cat > /tmp/data/User/settings.json <<'EOF'
{"workbench.startupEditor":"none","workbench.colorTheme":"Default Dark Modern","security.workspace.trust.enabled":false,"extensions.autoUpdate":false,"extensions.autoCheckUpdates":false,"telemetry.telemetryLevel":"off","window.dialogStyle":"custom"}
EOF
exec /opt/code-server/bin/code-server --bind-addr 0.0.0.0:8080 \
  --auth none --disable-telemetry --disable-update-check --disable-proxy \
  --disable-file-downloads --disable-file-uploads \
  --user-data-dir /tmp/data --extensions-dir /tmp/extensions /workspace
