# Read by non-interactive Bash via BASH_ENV and interactive Bash via ~/.bashrc.
if [ "${CLAUDE_DEVCONTAINER_NIX:-0}" = 1 ] && [ -f /workspace/.devcontainer/.nix-env/env.rc ]; then
    # env.rc ends by making a directory under TMPDIR and pointing TMPDIR, TMP,
    # TEMP and TEMPDIR at it. Every Bash reads this file, so nested shells would
    # pile up directories and lengthen TMPDIR until Chromium crashes. Undo it.
    old_tmpdir=${TMPDIR-}
    . /workspace/.devcontainer/.nix-env/env.rc
    rmdir "$NIX_BUILD_TOP"
    unset NIX_BUILD_TOP TMP TEMP TEMPDIR
    if [ -n "$old_tmpdir" ]; then TMPDIR=$old_tmpdir; else unset TMPDIR; fi
    unset old_tmpdir
fi
