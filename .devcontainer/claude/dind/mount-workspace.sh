#!/bin/sh
# Show the agent's /workspace, mounted at /srv/workspace, at /workspace through
# bindfs. Run as root before rootless dockerd starts, whose mount namespace
# then holds it. Usage: mount-workspace.sh UID GID (the agent's).
#
# Rootless dockerd maps a container's root to the agent's UID, but its other
# users to subordinate IDs (100000 + UID - 1). On the volume itself these could
# not write the agent's files, and what they wrote the agent could not manage.
# Through bindfs every file is created as the agent's, chown is ignored, and
# all may read and write. The container users that --mirror lists see
# themselves as the owners of every file, so that chmod and ownership checks
# such as PostgreSQL's pass: UIDs 1 to 18000, which is all that fits in one
# argument, and nobody (65534).
set -eu
uid=$1 gid=$2
[ -d /srv/workspace ] || exit 0
mirror="$(seq -s, 100000 117999),165533"
# --create-with-perms: bindfs ignores the umask of what it creates.
if ! bindfs --create-for-user="$uid" --create-for-group="$gid" --create-with-perms=go-w \
        --perms=a+rwX --chown-ignore \
        --mirror="$mirror" /srv/workspace /workspace; then
    echo "warning: bindfs failed; DinD's containers see /workspace as on the volume." >&2
    mount --bind /srv/workspace /workspace
fi
