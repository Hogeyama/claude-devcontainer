# Read by non-interactive Bash via BASH_ENV and interactive Bash via ~/.bashrc.
if [ "${CLAUDE_DEVCONTAINER_NIX:-0}" = 1 ] && [ -f /workspace/.devcontainer/.nix-env/env.rc ]; then
    . /workspace/.devcontainer/.nix-env/env.rc
fi
