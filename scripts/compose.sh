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
root=${CLAUDE_DEVCONTAINER_WORKSPACE:-$(cd "$scripts/../../.." && pwd -P)}
# The host command's environments use the core's own compose file.
if [ -n "${CLAUDE_DEVCONTAINER_CONFIG_DIR-}" ]; then
    file=$scripts/../host/compose.yaml
else
    file=$root/.devcontainer/compose.yaml
fi
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
compose() {
    exec docker compose -p "$projects" -f "$file" "$@"
}
# initializeCommand does not run here; bring .generated/ up to date with
# claude.toml as it would.
if python3 "$scripts/settings.py" generate; then
    case "$1" in
    up|create|start|restart)
        python3 "$scripts/settings.py" check-shared-mounts ;;
    esac
    compose "$@"
fi
# A setting in the environment or .devcontainer/.env, which generate refuses,
# would win over the previous .generated/settings.env. So go on only to stop or
# inspect the environment, without them: --env-file replaces .env.
case "$1" in
down|stop|kill|ps|logs)
    echo "warning: the settings are invalid; running $1 with the previous ones." >&2
    for name in $(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import settings; print(*settings.VARIABLES)' \
            "$scripts"); do
        unset "$name"
    done
    exec docker compose --env-file /dev/null -p "$projects" -f "$file" "$@" ;;
*)
    echo "error: fix the settings first; only down, stop, kill, ps and logs run with invalid ones." >&2
    exit 1 ;;
esac
