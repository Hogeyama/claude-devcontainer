#!/bin/sh
# Installs this checkout's committed core for the host command, which runs
# the agent for a repository without .devcontainer (scripts/claude-devcontainer):
# as ~/.local/share/claude-devcontainer/<tree>/, with current pointing to it,
# and the command in ~/.local/bin. Earlier versions are not removed. Every
# repository's next claude-devcontainer uses the new one, which refuses an
# environment still running from an earlier one until it is stopped.
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
# As the labels below name it: claude-devcontainer resolves the core's path.
data=$(cd "$data" && pwd -P)
if [ ! -d "$data/$tree" ]; then
    temporary=$(mktemp -d "$data/.install-XXXXXX")
    git -C "$top" archive "HEAD:$core" | tar -x -C "$temporary"
    mv "$temporary" "$data/$tree"
fi
ln -sfn "$tree" "$data/current"
ln -sfn "$data/current/scripts/claude-devcontainer" "$HOME/.local/bin/claude-devcontainer"
echo "Installed $data/$tree; run claude-devcontainer in a Git repository."
# Environments started from an earlier version: claude-devcontainer down
# stops each, from its repository.
others=$(docker ps -a --filter 'label=com.docker.compose.service=agent' \
    --format '{{.Label "devcontainer.config_file"}}	{{.Label "devcontainer.local_folder"}}' 2>/dev/null |
    awk -F '\t' -v data="$data/" -v own="$data/$tree/" 'index($1, data) == 1 && index($1, own) != 1 { print "  " $2 }' |
    sort -u) || true
if [ -n "$others" ]; then
    echo "These repositories run an environment of an earlier version; run claude-devcontainer down in each:"
    echo "$others"
fi
