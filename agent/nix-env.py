#!/usr/bin/env python3
"""Run the dev shell once and write what it exports to the env.rc the agent's Bash reads.

The host writes the script `nix print-dev-env` prints to DEV_ENV: the one that
`nix develop` reads, which sets the dev shell's variables and functions, makes
a TMPDIR and runs shellHook. As in `nix develop -c claude`, Bash sources it in
the workspace, and the dev shell is what Bash then exports: a command started
from it sees nothing else. devshell (numtide/devshell), for one, sets PATH and
its variables only in shellHook, and PRJ_ROOT from the directory it runs in.

The agent reads env.rc in every Bash, so shellHook runs here once, with its
output in LOG, and env.rc holds only what the dev shell changed in the
environment, less what only a build or that one shell needs (TZ=UTC,
SOURCE_DATE_EPOCH, TMPDIR, out, name, ...). Functions and aliases are not kept.

Usage: nix-env.py (as node, once the workspace is in place)
"""
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile

DEV_ENV = '/run/host/nix-env/dev-env.sh'
# Node's tmpfs: a restart runs the dev shell again from the host's DEV_ENV.
ENV_RC = '/run/claude-devcontainer/nix-env.rc'
LOG = '/run/claude-devcontainer/nix-env.log'
WORKSPACE = '/workspace'
# `devcontainer up` waits for the agent's startup, and with it for shellHook.
TIMEOUT_SECONDS = 120
# Dropped by `nix develop` itself, only meaningful inside a build, set by Bash
# for each shell, or the agent's own BASH_ENV, which reads env.rc.
DROP = {
    'BASHOPTS', 'HOME', 'NIX_BUILD_TOP', 'NIX_ENFORCE_PURITY', 'NIX_LOG_FD', 'NIX_REMOTE',
    'PPID', 'SHELL', 'SHELLOPTS', 'SSL_CERT_FILE', 'TEMP', 'TEMPDIR', 'TERM', 'TMP', 'TMPDIR',
    'TZ', 'UID', 'OLDPWD', 'CONFIG_SHELL', 'HOST_PATH', 'NIX_BUILD_CORES', 'NIX_STORE',
    'SOURCE_DATE_EPOCH', 'DETERMINISTIC_BUILD', 'PYTHONHASHSEED', 'PWD', 'SHLVL', '_', 'BASH_ENV',
}
# Sources DEV_ENV ($1) as `nix develop` does, and prints the environment unless
# the script exits. /usr/bin/env, as the dev shell's PATH may lack env.
SOURCE = '. "$1" </dev/null >"$2" 2>&1; status=$?; /usr/bin/env -0; exit "$status"'


def kept(name):
    # Derivation attributes (out, name, buildInputs, __structuredAttrs, ...)
    # start in lowercase or with __; exported functions are BASH_FUNC_name%%.
    return (name not in DROP and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name) is not None
            and not name[:1].islower() and not name.startswith('__'))


def dev_shell(script, base, workspace, log, timeout):
    """Source script in Bash started in workspace with base.

    Return the environment Bash exports afterwards and its exit status, with
    no environment if the script exits, and neither if it runs past timeout.
    """
    with tempfile.TemporaryDirectory(prefix='nix-env-') as tmp:
        env = {name: value for name, value in base.items() if name != 'BASH_ENV'}
        env.update(IN_NIX_SHELL='impure', TMPDIR=tmp)
        bash = subprocess.Popen(['bash', '--norc', '--noprofile', '-c', SOURCE, 'bash', script, log],
                                cwd=workspace, env=env, stdout=subprocess.PIPE, start_new_session=True)
        try:
            output, _ = bash.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(bash.pid, signal.SIGKILL)
            bash.communicate()
            return None, None
    if not output:
        return None, bash.returncode
    variables = (os.fsdecode(entry).partition('=') for entry in output.split(b'\0') if entry)
    return {name: value for name, _, value in variables}, bash.returncode


def env_rc(base, dev_env):
    """The Bash that turns base into dev_env, kept to what env.rc should hold."""
    lines = []
    for name in sorted(base.keys() | dev_env.keys()):
        before, value = base.get(name), dev_env.get(name)
        if value == before or not kept(name):
            continue
        if value is None:
            lines.append(f'unset {name}')
        elif before and value.endswith(':' + before):
            # A search path such as PATH: the agent's own entries stay after the dev shell's.
            prefix = shlex.quote(value[:-len(before) - 1])
            lines.append(f'export {name}={prefix}"${{{name}:+:${name}}}"')
        else:
            lines.append(f'export {name}={shlex.quote(value)}')
    return ''.join(line + '\n' for line in lines)


def main():
    base = dict(os.environ)
    dev_env, status = dev_shell(DEV_ENV, base, WORKSPACE, LOG, TIMEOUT_SECONDS)
    if dev_env is None:
        reason = f'exited with {status}' if status is not None else f'did not finish within {TIMEOUT_SECONDS}s'
        print(f'warning: the nix dev shell {reason}; Bash runs without it. Its output is in {LOG}.',
              file=sys.stderr)
        return
    if status:
        print(f'warning: the nix dev shell exited with {status}; see {LOG}.', file=sys.stderr)
    temporary = f'{ENV_RC}.tmp'
    with open(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as file:
        file.write(env_rc(base, dev_env))
    os.replace(temporary, ENV_RC)


if __name__ == '__main__':
    main()
