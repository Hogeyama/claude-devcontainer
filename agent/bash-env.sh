# Read by non-interactive Bash via BASH_ENV and interactive Bash via ~/.bashrc.
# nix-env.py writes the dev shell's environment as node at startup. Only its
# owner reads it, so that Bash run as root does not take what node can change.
if [ "${CLAUDE_DEVCONTAINER_NIX:-0}" = 1 ] && [ -O /run/claude-devcontainer/nix-env.rc ]; then
    . /run/claude-devcontainer/nix-env.rc
fi
