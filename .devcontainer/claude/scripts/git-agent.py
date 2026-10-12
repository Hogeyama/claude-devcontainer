#!/usr/bin/env python3
"""Register and serve the host's direct Git remote to its running agent."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]


def remote_url():
    # Remotes are shared by linked worktrees. Resolve the invoking worktree at
    # connection time, rather than embedding one checkout's path in the URL.
    # This fixed shell command quotes the resolved path; no agent data is code.
    command = 'exec python3 "$(git rev-parse --show-toplevel)/.devcontainer/claude/scripts/git-agent.py" "$@"'
    # git-remote-ext uses its own percent escaping, not shell quoting.
    return 'ext::sh -c ' + command.replace('%', '%%').replace(' ', '% ') + ' -- %S'


def register(root=ROOT):
    def git(*args, check=True):
        return subprocess.run(['git', '-C', str(root), *args], check=check,
                              capture_output=True, text=True)

    url = remote_url()
    current = git('config', '--get-all', 'remote.agent.url', check=False)
    if current.returncode not in (0, 1):
        raise ValueError('Could not read the agent remote configuration.')
    if current.returncode == 0:
        if current.stdout.splitlines() != [url]:
            raise ValueError('Remote agent already points elsewhere; rename it before starting this environment.')
        for name in ('pushurl', 'vcs'):
            if git('config', '--get-all', f'remote.agent.{name}', check=False).returncode == 0:
                raise ValueError(f'Remove remote.agent.{name} before using the direct container remote.')
    else:
        git('remote', 'add', 'agent', url)
    # Enable the built-in pipe transport only in this trusted host repository.
    git('config', '--local', 'protocol.ext.allow', 'always')


def serve(service, root=ROOT):
    if service not in ('git-upload-pack', 'git-receive-pack'):
        raise ValueError('Only Git upload-pack and receive-pack are supported.')
    containers = subprocess.run(
        ['docker', 'ps', '--filter', f'label=devcontainer.local_folder={root}',
         '--filter', 'label=com.docker.compose.service=agent', '--format', '{{.ID}}'],
        check=True, capture_output=True, text=True).stdout.split()
    if len(containers) != 1:
        raise ValueError('Expected exactly one running agent container for this workspace; run devcontainer up.')
    # stdout/stdin belong exclusively to Git's wire protocol. No shell, TTY,
    # root Git process, host-side checkout, or exposed TCP listener is involved.
    os.execvp('docker', ['docker', 'exec', '-i', '--user', 'node', containers[0], service, '/workspace'])


if __name__ == '__main__':
    try:
        if sys.argv[1:] == ['register']:
            register()
        elif len(sys.argv) == 2:
            serve(sys.argv[1])
        else:
            raise ValueError('usage: git-agent.py register|git-upload-pack|git-receive-pack')
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        sys.exit(f'error: {error}')
