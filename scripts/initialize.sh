#!/bin/sh
# Runs on the host before the containers start (initializeCommand), from the
# workspace root.
set -eu
# Check .devcontainer/claude.toml and claude.local.toml, and write what compose,
# the proxy build and the scripts below read to .devcontainer/.generated/.
python3 "$(dirname "$0")/settings.py" generate
python3 "$(dirname "$0")/workspace-source.py"
python3 "$(dirname "$0")/git-agent.py" register
python3 "$(dirname "$0")/settings.py" prepare-dependencies
mkdir -p .devcontainer/.nix-env

# A setting as settings.py wrote it for compose: NAME='value', never with '.
setting() {
    sed -n "s/^$1='\\(.*\\)'\$/\\1/p" .devcontainer/.generated/settings.env
}
CLAUDE_DEVCONTAINER_DIND=$(setting CLAUDE_DEVCONTAINER_DIND)

# compose.yaml reads the internal subnets from .generated/networks.env unless
# claude.toml sets them; a free pair is chosen once and kept.
python3 "$(dirname "$0")/pick-networks.py" \
    "$(setting CLAUDE_DEVCONTAINER_PROXY_NET)" "$(setting CLAUDE_DEVCONTAINER_DIND_NET)"

# Export the trusted host commit's dev shell to a read-only management mount.
# The agent's independent refs and worktree cannot change this input.
if [ "$(setting CLAUDE_DEVCONTAINER_NIX)" = 1 ]; then
    out=.devcontainer/.nix-env
    ref=$(setting CLAUDE_DEVCONTAINER_NIX_REF)
    shell=$(setting CLAUDE_DEVCONTAINER_NIX_SHELL)
    mkdir -p "$out"
    echo '*' > "$out/.gitignore"
    if ! rev=$(git rev-parse --verify --quiet "$ref^{commit}"); then
        echo "warning: nix.ref = $ref is not a commit; the dev shell is not updated." >&2
    # --profile makes a GC root, so that the host's garbage collection keeps it.
    elif nix --extra-experimental-features 'nix-command flakes' print-dev-env --json --no-write-lock-file \
            --profile "$out/profile" "git+file://$(pwd -P)?rev=$rev${shell:+#$shell}" >"$out/env.json.tmp" &&
        "$(dirname "$0")/nix-env.py" <"$out/env.json.tmp" >"$out/env.rc.tmp"; then
        mv "$out/env.rc.tmp" "$out/env.rc"
        rm -f "$out/env.json.tmp"
    else
        rm -f "$out/env.json.tmp" "$out/env.rc.tmp"
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
