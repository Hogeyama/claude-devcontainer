#!/bin/sh
# Owned by this workspace: prepare the copy-on-write dependency overlays that
# compose.yaml mounts into the agent. Runs on the host from devcontainer.json's
# initializeCommand, with the repository root as the working directory.
set -eu

mkdir -p \
    "$HOME/.gradle" \
    "$HOME/.local/state/claude-devcontainer/gradle-home/upper" \
    "$HOME/.local/state/claude-devcontainer/gradle-home/work" \
    frontend/node_modules \
    "$HOME/.local/state/claude-devcontainer/frontend-node-modules/upper" \
    "$HOME/.local/state/claude-devcontainer/frontend-node-modules/work"

# The overlay's lowerdir must be an absolute path, and this repository can be
# cloned anywhere. Write it to .devcontainer/.env, which docker compose reads
# for interpolation next to compose.yaml. CLAUDE_DEVCONTAINER_* names are
# reserved for settings.py; anything else is free.
printf 'DEMO_FRONTEND_NODE_MODULES=%s\n' "$PWD/frontend/node_modules" > .devcontainer/.env
