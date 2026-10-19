#!/bin/sh
# Installs this checkout's committed core for the host command, which runs
# the agent for a repository without .devcontainer (scripts/claude-devcontainer):
# as ~/.local/share/claude-devcontainer/<tree>/, with current pointing to it,
# and the command in ~/.local/bin. Earlier versions stay: an environment
# started from one still uses its compose files.
#
# Usage: sh .devcontainer/claude/scripts/install-host.sh
set -eu
scripts=$(cd "$(dirname "$0")" && pwd -P)
top=$(git -C "$scripts" rev-parse --show-toplevel)
core=$(git -C "$scripts" rev-parse --show-prefix)
core=${core%scripts/}
core=${core%/}
# The committed tree, so that the installed core is one that Git can name.
tree=$(git -C "$top" rev-parse --short=12 "HEAD:$core")
data=${XDG_DATA_HOME:-$HOME/.local/share}/claude-devcontainer
mkdir -p "$data" "$HOME/.local/bin"
if [ ! -d "$data/$tree" ]; then
    temporary=$(mktemp -d "$data/.install-XXXXXX")
    git -C "$top" archive "HEAD:$core" | tar -x -C "$temporary"
    mv "$temporary" "$data/$tree"
fi
ln -sfn "$tree" "$data/current"
ln -sfn "$data/current/scripts/claude-devcontainer" "$HOME/.local/bin/claude-devcontainer"
echo "Installed $data/$tree; run claude-devcontainer in a Git repository."
