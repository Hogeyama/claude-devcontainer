#!/bin/sh
# Runs `docker compose` on the host for this workspace's environment, as
# started by Dev Containers: finds the Compose project from the agent
# container and passes the arguments on.
#
# Usage: .devcontainer/claude/scripts/compose.sh down
#        .devcontainer/claude/scripts/compose.sh logs proxy
set -eu
if [ $# -eq 0 ]; then
    echo "usage: $0 <docker compose arguments>, e.g. down, logs proxy" >&2
    exit 2
fi
scripts=$(cd "$(dirname "$0")" && pwd -P)
. "$scripts/compose-context.sh"
prepare_compose "$@"
compose "$@"
