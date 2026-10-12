#!/usr/bin/env python3
"""Export the local starting commit for the agent's first independent clone."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]


def write(root=ROOT):
    root = Path(root)
    def git(*args):
        return subprocess.run(['git', '-C', str(root), *args], check=True,
                              capture_output=True, text=True).stdout.strip()
    try:
        commit = git('rev-parse', '--verify', 'HEAD^{commit}')
    except subprocess.CalledProcessError:
        raise ValueError('A committed HEAD is required.') from None
    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', commit):
        raise ValueError('HEAD did not resolve to a full commit ID.')
    data = {'commit': commit, 'seed': f'workspace-{commit}.git'}
    directory = root / '.devcontainer/.generated'
    directory.mkdir(parents=True, exist_ok=True)
    seed = directory / data['seed']
    if not seed.exists():
        # Git's local transport copies reachable objects without copying config,
        # hooks, index, alternates, or hardlinks. This also handles linked worktrees.
        # Keep each published snapshot immutable so interrupted imports can retry.
        environment = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
        with tempfile.TemporaryDirectory(prefix='.workspace-', dir=directory) as temporary:
            repository = Path(temporary) / 'repo.git'
            def export(*args):
                return subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', *args],
                                      env=environment, check=True, capture_output=True, text=True)
            export('init', '--bare', '--template=',
                   '--object-format=' + ('sha256' if len(commit) == 64 else 'sha1'), str(repository))
            export('-C', str(repository), 'fetch', '--no-tags', '--no-recurse-submodules', '--update-shallow',
                   str(root.resolve()), commit + ':refs/heads/bootstrap')
            if export('-C', str(repository), 'rev-parse', '--verify', 'refs/heads/bootstrap^{commit}').stdout.strip() != commit:
                raise ValueError('Local Git snapshot does not match the starting commit.')
            export('-C', str(repository), 'symbolic-ref', 'HEAD', 'refs/heads/bootstrap')
            repository.rename(seed)
    temporary = directory / 'workspace.json.tmp'
    temporary.write_text(json.dumps(data) + '\n')
    temporary.replace(directory / 'workspace.json')
    return data


if __name__ == '__main__':
    try:
        write()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        sys.exit(f'error: {error}')
