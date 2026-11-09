#!/bin/sh
# Print /etc/subuid or /etc/subgid lines for rootless that give its containers
# the IDs 1-65536 as before, from the subordinate IDs 100000-165535, except
# that the container's ID $1 is the same ID outside: the agent's, so that files
# a container writes as that user are the agent's (docker.map_agent_ids).
# rootlesskit maps the ranges in file order after its own ID, which is root.
# Usage: subids.sh ID   (1 to 65535)
set -eu
id=$1
if [ "$id" -lt 1 ] || [ "$id" -gt 65535 ]; then
    echo "error: $id is not an ID of 1 to 65535." >&2
    exit 1
fi
if [ "$id" -gt 1 ]; then
    echo "rootless:100000:$((id - 1))"
fi
echo "rootless:$id:1"
echo "rootless:$((100000 + id)):$((65536 - id))"
