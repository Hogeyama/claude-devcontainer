# Read by non-interactive Bash via BASH_ENV and interactive Bash via ~/.bashrc.
if [ "${CLAUDE_DEVCONTAINER_NIX:-0}" = 1 ] && [ -f /run/host/nix-env/env.rc ]; then
    . /run/host/nix-env/env.rc
fi
