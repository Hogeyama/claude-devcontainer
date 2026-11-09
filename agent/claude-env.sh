# CLAUDE_ENV_FILE: Claude Code runs this before each command of its Bash tool.
# That Bash reads /etc/bash-env.sh through BASH_ENV, but then sources Claude
# Code's shell snapshot, which puts back the PATH saved when the snapshot was
# taken, without the nix dev shell's and agent.extra_path's entries. Read
# bash-env.sh again after it; what it prepends is left alone when it is there.
unset claude_devcontainer_env_read
. /etc/bash-env.sh
