#!/usr/bin/env python3
"""Turn `nix print-dev-env --json` into the env.rc the agent's Bash reads.

The Bash script `nix print-dev-env` prints is meant for one `nix develop`
shell: it sets everything the build of the shell would see (TZ=UTC,
SOURCE_DATE_EPOCH=1980, SHELL, out, name, ...), makes a new TMPDIR, and runs
shellHook. The agent reads env.rc in every Bash, so all of that would reach
every command. Only the exported variables that a dev shell is for are kept:
the flake's own ones, PATH and what the packages' setup hooks add. Functions
and shellHook are left out.

Usage: nix print-dev-env --json ... | nix-env.py > env.rc
"""
import json
import shlex
import sys

# Dropped by `nix develop` itself, or only meaningful inside a build.
DROP = {
    'BASHOPTS', 'HOME', 'NIX_BUILD_TOP', 'NIX_ENFORCE_PURITY', 'NIX_LOG_FD', 'NIX_REMOTE',
    'PPID', 'SHELL', 'SHELLOPTS', 'SSL_CERT_FILE', 'TEMP', 'TEMPDIR', 'TERM', 'TMP', 'TMPDIR',
    'TZ', 'UID', 'OLDPWD', 'CONFIG_SHELL', 'HOST_PATH', 'NIX_BUILD_CORES', 'NIX_STORE',
    'SOURCE_DATE_EPOCH', 'DETERMINISTIC_BUILD', 'PYTHONHASHSEED',
}
# Searched in order; the agent's own entries stay after the dev shell's.
PREPEND = {'PATH', 'XDG_DATA_DIRS'}


def kept(name):
    # Derivation attributes (out, name, buildInputs, __structuredAttrs, ...)
    # start in lowercase or with __.
    return name not in DROP and not name[:1].islower() and not name.startswith('__')


def env_rc(dev_env):
    lines = []
    for name, variable in sorted(dev_env['variables'].items()):
        if variable['type'] != 'exported' or not kept(name):
            continue
        value = shlex.quote(variable['value'])
        if name in PREPEND:
            lines.append(f'export {name}={value}"${{{name}:+:${name}}}"')
        else:
            lines.append(f'export {name}={value}')
    return ''.join(line + '\n' for line in lines)


if __name__ == '__main__':
    sys.stdout.write(env_rc(json.load(sys.stdin)))
