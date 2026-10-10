#!/bin/sh
# postStartCommand: return once the agent is ready for work, so that `devcontainer
# up` does not hand it over half-started.
set -eu
dind=${CLAUDE_DEVCONTAINER_DIND:-0}
if [ "$dind" = 1 ]; then
    timeout 60 sh -c 'until docker info >/dev/null 2>&1; do sleep 0.2; done'
fi
# Until the broker listens, the sumi hooks refuse every tool result.
if [ "${CLAUDE_DEVCONTAINER_SUMI:-0}" = 1 ] &&
    ! timeout 30 sh -c 'until [ -S /run/sumi/mask.sock ]; do sleep 0.2; done'; then
    echo "warning: the sumi service is not running; tools fail until it does." \
        "Check: docker compose logs sumi" >&2
fi
# initializeCommand copies the host's configuration in once this container
# starts (scripts/copy-host-config.py).
markers=/run/claude-devcontainer
if ! timeout 30 sh -c 'until [ -e "$1" ]; do sleep 0.2; done' sh "$markers/host-config-copied"; then
    echo "warning: the host's configuration was not copied in." \
        "Run .devcontainer/claude/scripts/copy-host-config.py on the host." >&2
fi
# initializeCommand also loads the images in .devcontainer/images.txt into DinD
# (scripts/load-images.py), and names its marker after the list's content.
list=/workspace/.devcontainer/images.txt
if [ "$dind" = 1 ] && [ -n "$(sed 's/#.*//' "$list" 2>/dev/null | tr -d ' \t\r\n')" ]; then
    marker="$markers/images-$(sha256sum <"$list" | cut -c1-16)"
    # Large images take minutes, so wait long only once loading has started.
    if ! timeout 60 sh -c 'until [ -e "$1.started" ] || [ -e "$1" ]; do sleep 0.5; done' sh "$marker" ||
        ! timeout 600 sh -c 'until [ -e "$1" ]; do sleep 0.5; done' sh "$marker"; then
        echo "warning: the images in .devcontainer/images.txt were not loaded into DinD." \
            "Run .devcontainer/claude/scripts/load-images.py on the host." >&2
    elif [ -s "$marker" ]; then
        echo "warning: some images in .devcontainer/images.txt were not loaded into DinD:" >&2
        cat "$marker" >&2
    fi
fi
