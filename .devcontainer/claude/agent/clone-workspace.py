#!/usr/bin/env python3
"""Initialize an independent clone once, without replacing user work."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from workspace_mounts import dependency_mounts


def inputs(path):
    data = json.loads(Path(path).read_text())
    if (not isinstance(data, dict) or set(data) not in ({'commit', 'seed'}, {'commit', 'seed', 'branch'})
            or not all(isinstance(v, str) for v in data.values())):
        raise ValueError('Invalid workspace clone metadata.')
    if (not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', data['commit'])
            or data['seed'] != f"workspace-{data['commit']}.git"):
        raise ValueError('Invalid workspace clone metadata.')
    branch = data.get('branch', '')
    if branch and (branch.startswith('-') or branch == 'HEAD' or subprocess.run(
            ['git', 'check-ref-format', 'refs/heads/' + branch], capture_output=True).returncode):
        raise ValueError('Invalid workspace branch metadata.')
    return data


def empty_except_mounts(workspace, mounts):
    ancestors = {parent for mount in mounts for parent in mount.parents if parent != workspace}
    def check(directory):
        for path in directory.iterdir():
            if path in mounts:
                continue
            if path.is_symlink() or path not in ancestors or not path.is_dir() or not check(path):
                return False
        return True
    return check(workspace)


def clone(source, workspace):
    workspace = Path(workspace).absolute()
    workspace.mkdir(parents=True, exist_ok=True)
    marker = workspace / '.git/claude-bootstrap.json'
    environment = dict(os.environ, GIT_TERMINAL_PROMPT='0')
    mounts = dependency_mounts(workspace)

    def git(*args, check=True, timeout=30):
        return subprocess.run(['git', '-C', str(workspace), *args],
                              env=environment, check=check, timeout=timeout,
                              capture_output=True, text=True)

    if marker.is_file():
        data = inputs(marker)
    elif (workspace / '.git').is_dir():
        if git('rev-parse', '--verify', 'HEAD^{commit}', check=False).returncode == 0:
            return
        raise ValueError('Incomplete repository without initialization metadata; inspect /workspace.')
    else:
        if not empty_except_mounts(workspace, mounts):
            raise ValueError('Workspace is not empty; inspect it before initializing a clone.')
        data = inputs(source)
        # Record intent before git init, so interruptions can be retried.
        marker.parent.mkdir()
        marker.write_text(json.dumps(data) + '\n')

    git('init', '--object-format=' + ('sha256' if len(data['commit']) == 64 else 'sha1'))
    seed = Path(source).absolute().parent / data['seed']
    try:
        git('-c', f'safe.directory={seed}', 'fetch', '--no-tags', '--no-recurse-submodules', '--update-shallow',
            str(seed), 'refs/heads/bootstrap', timeout=300)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise ValueError('Could not import the local Git snapshot. Run workspace-source.py on the host, '
                         'then run wait-ready.sh as node again.') from None
    if git('cat-file', '-e', data['commit'] + '^{commit}', check=False).returncode:
        raise ValueError('The local Git snapshot lacks the starting commit; inspect the host snapshot.')
    tracked = git('ls-tree', '-rz', '--name-only', data['commit']).stdout.split('\0')
    for name in filter(None, tracked):
        path = workspace / name
        if any(path == mount or path.is_relative_to(mount) or mount.is_relative_to(path) for mount in mounts):
            raise ValueError(f'Tracked path {name!r} conflicts with a dependency mount; inspect the starting commit.')
    branch = data.get('branch', '')
    if branch:
        existing = git('show-ref', '--verify', '--hash', 'refs/heads/' + branch, check=False)
        if existing.returncode == 0:
            if existing.stdout.strip() != data['commit']:
                raise ValueError('Starting branch changed during initialization; inspect /workspace before retrying.')
            # A prior checkout may have succeeded before the marker was removed.
            git('checkout', branch, '--')
        else:
            git('checkout', '-b', branch, data['commit'])
    else:
        git('checkout', '--detach', data['commit'])
    marker.unlink()


if __name__ == '__main__':
    if os.geteuid() == 0:
        sys.exit('error: run workspace initialization as node, never root.')
    try:
        clone('/run/host/generated/workspace.json', '/workspace')
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        sys.exit(f'error: workspace initialization failed: {error}')
