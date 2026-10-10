#!/bin/sh
# Runs on the host before the containers start (initializeCommand), from the
# workspace root.
set -eu
# compose.yaml mounts these read-only. Create them as the user, or Docker
# would create them as root on the host.
if [ ! -d .git ]; then
    echo "error: $(pwd) must be the root of a Git repository (not a worktree)." >&2
    exit 1
fi
mkdir -p .git/hooks .git/modules
# Never fail here: the environment works without the configuration.
"$(dirname "$0")/copy-host-config.py" --background ||
    echo "warning: could not copy the host's configuration into the agent." >&2
exec "$(dirname "$0")/inject-credentials.py" --background
