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
root=$(cd "$(dirname "$0")/../../.." && pwd -P)
# -a so that down also finds an environment whose containers have stopped.
projects=$(docker ps -a --filter "label=devcontainer.local_folder=$root" \
    --filter 'label=com.docker.compose.service=agent' \
    --format '{{.Label "com.docker.compose.project"}}' | sort -u)
case "$projects" in
'')
    echo "error: no environment of $root found; start it with 'devcontainer up'." >&2
    exit 1 ;;
*[![:alnum:]_.-]*)
    echo "error: more than one environment of $root found: $projects" >&2
    exit 1 ;;
esac
exec docker compose -p "$projects" -f "$root/.devcontainer/compose.yaml" "$@"
