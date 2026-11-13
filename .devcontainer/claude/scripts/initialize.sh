#!/bin/sh
# Runs on the host before the containers start (initializeCommand), from the
# workspace root.
set -eu
# Check .devcontainer/claude.toml and claude.local.toml, and write what compose,
# the proxy build and the scripts below read to .devcontainer/.generated/.
python3 "$(dirname "$0")/settings.py" generate
python3 "$(dirname "$0")/settings.py" prepare-dependencies
# .devcontainer/, or the host command's state directory (scripts/layout.py).
config=${CLAUDE_DEVCONTAINER_CONFIG_DIR:-.devcontainer}
mkdir -p "$config/.nix-env"

# A setting as settings.py wrote it for compose: NAME='value', never with '.
setting() {
    sed -n "s/^$1='\\(.*\\)'\$/\\1/p" "$config/.generated/settings.env"
}
CLAUDE_DEVCONTAINER_DIND=$(setting CLAUDE_DEVCONTAINER_DIND)
# Over docker.shared_base, add the images of docker.images that the base lacks
# as a new generation, and have DinD start over it. Without it, they still
# load into this workspace's DinD, as below.
if [ -n "$(setting CLAUDE_DEVCONTAINER_DIND_SHARED)" ]; then
    python3 "$(dirname "$0")/dind-base.py" --auto ||
        echo "warning: could not add docker.images to the shared DinD base; they load into this DinD instead." >&2
    python3 "$(dirname "$0")/settings.py" generate
fi
# Only independent Git modes need a snapshot and an agent remote.
if [ "$(setting CLAUDE_DEVCONTAINER_GIT_ISOLATION)" != unsafe_shared ]; then
    python3 "$(dirname "$0")/workspace-source.py"
    python3 "$(dirname "$0")/git-agent.py" register
fi

# compose.yaml reads the internal subnets from .generated/networks.env unless
# claude.toml sets them; a free pair is chosen once and kept.
python3 "$(dirname "$0")/pick-networks.py" \
    "$(setting CLAUDE_DEVCONTAINER_PROXY_NET)" "$(setting CLAUDE_DEVCONTAINER_DIND_NET)"
python3 "$(dirname "$0")/settings.py" check-shared-mounts

# Export the host commit's dev shell to a read-only management mount. In clone
# and shared, the agent's independent refs and worktree cannot change this
# input. In unsafe_shared the agent writes the host's HEAD and refs, so the
# flake is agent-written like the other shared files the host runs. It is the
# script `nix develop` reads; the agent runs it, shellHook included, once it
# starts (agent/nix-env.py).
if [ "$(setting CLAUDE_DEVCONTAINER_NIX)" = 1 ]; then
    out=$config/.nix-env
    ref=$(setting CLAUDE_DEVCONTAINER_NIX_REF)
    shell=$(setting CLAUDE_DEVCONTAINER_NIX_SHELL)
    mkdir -p "$out"
    echo '*' > "$out/.gitignore"
    if ! rev=$(git rev-parse --verify --quiet "$ref^{commit}"); then
        echo "warning: nix.ref = $ref is not a commit; the dev shell is not updated." >&2
    # --profile makes a GC root, so that the host's garbage collection keeps it.
    elif nix --extra-experimental-features 'nix-command flakes' print-dev-env --no-write-lock-file \
            --profile "$out/profile" "git+file://$(pwd -P)?rev=$rev${shell:+#$shell}" >"$out/dev-env.sh.tmp"; then
        mv "$out/dev-env.sh.tmp" "$out/dev-env.sh"
    else
        rm -f "$out/dev-env.sh.tmp"
        echo "warning: nix print-dev-env failed; the dev shell is not updated." >&2
    fi
fi

# Never fail here: the environment works without the configuration.
"$(dirname "$0")/copy-host-config.py" --background ||
    echo "warning: could not copy the host's configuration into the agent." >&2
if [ "$CLAUDE_DEVCONTAINER_DIND" = 1 ]; then
    "$(dirname "$0")/load-images.py" --background ||
        echo "warning: could not load the images of docker.images into DinD." >&2
fi
exec "$(dirname "$0")/inject-credentials.py" --background
