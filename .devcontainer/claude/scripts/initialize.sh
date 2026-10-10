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

# A setting from the environment, else from .devcontainer/.env, as compose reads it.
setting() {
    eval "value=\${$1-}"
    if [ -z "$value" ] && [ -f .devcontainer/.env ]; then
        value=$(sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" .devcontainer/.env | tail -n 1)
        value=${value%\"}; value=${value#\"}; value=${value%\'}; value=${value#\'}
    fi
    printf '%s' "${value:-$2}"
}
CLAUDE_DEVCONTAINER_DIND=$(setting CLAUDE_DEVCONTAINER_DIND 0)

# Export the flake's dev shell for the agent's bash to load from the read-only
# .devcontainer/. Evaluate a commit rather than the working tree, which the
# agent writes: evaluating fetches inputs and builds on the host, outside the
# proxy. A ref in .git can still be moved by the agent; see the README.
if [ "$(setting CLAUDE_DEVCONTAINER_NIX 0)" = 1 ]; then
    out=.devcontainer/.nix-env
    ref=$(setting CLAUDE_DEVCONTAINER_NIX_REF HEAD)
    shell=$(setting CLAUDE_DEVCONTAINER_NIX_SHELL '')
    mkdir -p "$out"
    echo '*' > "$out/.gitignore"
    if ! rev=$(git rev-parse --verify --quiet "$ref^{commit}"); then
        echo "warning: CLAUDE_DEVCONTAINER_NIX_REF=$ref is not a commit; the dev shell is not updated." >&2
    # --profile makes a GC root, so that the host's garbage collection keeps it.
    elif nix --extra-experimental-features 'nix-command flakes' print-dev-env --no-write-lock-file \
            --profile "$out/profile" "git+file://$(pwd -P)?rev=$rev${shell:+#$shell}" >"$out/env.rc.tmp"; then
        mv "$out/env.rc.tmp" "$out/env.rc"
    else
        rm -f "$out/env.rc.tmp"
        echo "warning: nix print-dev-env failed; the dev shell is not updated." >&2
    fi
fi

# Never fail here: the environment works without the configuration.
"$(dirname "$0")/copy-host-config.py" --background ||
    echo "warning: could not copy the host's configuration into the agent." >&2
if [ "$CLAUDE_DEVCONTAINER_DIND" = 1 ]; then
    "$(dirname "$0")/load-images.py" --background ||
        echo "warning: could not load the images in .devcontainer/images.txt into DinD." >&2
fi
exec "$(dirname "$0")/inject-credentials.py" --background
