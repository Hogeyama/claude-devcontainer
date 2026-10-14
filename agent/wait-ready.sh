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
# initializeCommand also loads the images of docker.images in claude.toml into
# DinD (scripts/load-images.py), and names its marker after the content of the
# list that scripts/settings.py wrote at `up`.
list=/run/host/generated/images.txt
if [ "$dind" = 1 ] && [ -n "$(sed 's/#.*//' "$list" 2>/dev/null | tr -d ' \t\r\n')" ]; then
    marker="$markers/images-$(sha256sum <"$list" | cut -c1-16)"
    if [ ! -e "$marker" ]; then
        echo "Waiting for docker.images to be loaded into DinD..." >&2
    fi
    # Large images take minutes, so wait long only once loading has started.
    if ! timeout 60 sh -c 'until [ -e "$1.started" ] || [ -e "$1" ]; do sleep 0.5; done' sh "$marker" ||
        ! timeout 600 sh -c 'until [ -e "$1" ]; do sleep 0.5; done' sh "$marker"; then
        echo "warning: the images of docker.images were not loaded into DinD." \
            "Run .devcontainer/claude/scripts/load-images.py on the host." >&2
    elif [ -s "$marker" ]; then
        echo "warning: some images of docker.images were not loaded into DinD:" >&2
        cat "$marker" >&2
    fi
fi

# Git runs only after the entrypoint dropped privileges, as remoteUser node.
case "${CLAUDE_DEVCONTAINER_GIT_ISOLATION:-clone}" in
clone)
    echo 'Git isolation: clone' >&2
    python3 /usr/local/bin/clone-workspace.py ;;
shared)
    echo 'Git isolation: shared; edits reach the host; .git and .devcontainer are read-only. Commit, switch and stash on the host.' >&2
    python3 /usr/local/bin/workspace_mounts.py --check-shared ;;
worktree)
    echo 'Git isolation: worktree; edits reach the host; .git is this container'"'"'s own, with the host'"'"'s read-only at /run/host/git. Commit here; the host fetches and accepts.' >&2
    python3 /usr/local/bin/workspace_mounts.py --check-shared
    python3 /usr/local/bin/clone-workspace.py ;;
*)
    echo 'error: unknown CLAUDE_DEVCONTAINER_GIT_ISOLATION' >&2
    exit 1 ;;
esac
# Like `nix develop -c claude`: run the dev shell, shellHook included, in the
# workspace now in place, for every Bash to read what it exports.
if [ "${CLAUDE_DEVCONTAINER_NIX:-0}" = 1 ] && [ -f /run/host/nix-env/dev-env.sh ]; then
    python3 /usr/local/bin/nix-env.py ||
        echo "warning: the nix dev shell could not be loaded; Bash runs without it." >&2
fi
