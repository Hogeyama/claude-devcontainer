#!/bin/sh
# postStartCommand: return once the agent is ready for work, so that `devcontainer
# up` does not hand it over half-started.
set -eu
timeout 60 sh -c 'until docker info >/dev/null 2>&1; do sleep 0.2; done'
# initializeCommand copies the host's Claude Code configuration in once this
# container starts (scripts/copy-claude-config.py).
if ! timeout 30 sh -c 'until [ -e "$HOME/.claude/.host-config-copied" ]; do sleep 0.2; done'; then
    echo "warning: the host's Claude Code configuration was not copied in." \
        "Run .devcontainer/claude/scripts/copy-claude-config.py on the host." >&2
fi
