# Read by non-interactive Bash via BASH_ENV, and via the top of ~/.bashrc by
# interactive Bash and by Bash that takes itself for a command run by rshd (see
# the Dockerfile). A login Bash reads both, as ~/.profile reads ~/.bashrc.
# Exec sessions do not inherit the entrypoint's umask. Set it here too, so
# editor terminals and Claude's Bash tool create files as 0644, directories 0755.
umask 022
if [ -z "${claude_devcontainer_env_read-}" ]; then
    claude_devcontainer_env_read=1
    # nix-env.py writes the dev shell's environment as node at startup. Only its
    # owner reads it, so that Bash run as root does not take what node can change.
    if [ "${CLAUDE_DEVCONTAINER_NIX:-0}" = 1 ] && [ -O /run/claude-devcontainer/nix-env.rc ]; then
        . /run/claude-devcontainer/nix-env.rc
    fi
fi
