#!/usr/bin/env python3
"""Register and serve the host's direct Git remote to its running agent."""
import os
import json
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
    # Enable the built-in pipe transport only in this trusted host repository,
    # and only for commands run directly: `user` refuses it to those Git runs on
    # its own, such as the clone of a submodule whose URL .gitmodules names.
    git('config', '--local', 'protocol.ext.allow', 'user')


def check_container(container, root=ROOT):
    """The mode of the running container, never the TOML edited for its next creation."""
    result = subprocess.run(['docker', 'inspect', container], check=True, capture_output=True, text=True)
    details = json.loads(result.stdout)[0]
    labels = details.get('Config', {}).get('Labels') or {}
    mode = labels.get('dev.claude-devcontainer.git-isolation')
    mounts = {mount['Destination']: mount for mount in details.get('Mounts', [])}
    workspace = mounts.get('/workspace', {})
    project = labels.get('com.docker.compose.project')
    if mode == 'unsafe_shared':
        git = mounts.get('/workspace/.git', {})
        if (not project or workspace.get('Type') != 'bind' or not workspace.get('RW')
                or Path(workspace.get('Source', '')) != Path(root).resolve()
                or git.get('Type') != 'bind' or not git.get('RW')
                or Path(git.get('Source', '')) != Path(root).resolve() / '.git'):
            raise ValueError('Cannot verify the agent unsafe_shared mounts; recreate the container.')
        return mode
    if mode == 'shared':
        git = mounts.get('/workspace/.git', {})
        if (not project or workspace.get('Type') != 'bind' or not workspace.get('RW')
                or Path(workspace.get('Source', '')) != Path(root).resolve()
                or git.get('Type') != 'volume' or git.get('Name') != project + '_agent-git' or not git.get('RW')):
            raise ValueError('Cannot verify the agent shared mounts; recreate it with devcontainer up '
                             '--remove-existing-container.')
        return mode
    if (mode not in (None, 'clone') or not project
            or workspace.get('Type') != 'volume' or workspace.get('Name') != project + '_workspace'
            or not workspace.get('RW')):
        raise ValueError('Cannot verify the agent clone workspace mount; recreate it with devcontainer up '
                         '--remove-existing-container.')
    return 'clone'


def running_container(root=ROOT):
    containers = subprocess.run(
        ['docker', 'ps', '--filter', f'label=devcontainer.local_folder={root}',
         '--filter', 'label=com.docker.compose.service=agent', '--format', '{{.ID}}'],
        check=True, capture_output=True, text=True).stdout.split()
    if len(containers) != 1:
        raise ValueError('Expected exactly one running agent container for this workspace; run devcontainer up.')
    return containers[0]


def serve(service, root=ROOT):
    if service not in ('git-upload-pack', 'git-receive-pack'):
        raise ValueError('Only Git upload-pack and receive-pack are supported.')
    container = running_container(root)
    mode = check_container(container, root)
    if mode == 'unsafe_shared':
        raise ValueError('unsafe_shared uses the host Git directly; the agent remote is unnecessary.')
    command = [service]
    if service == 'git-receive-pack':
        if mode == 'shared':
            raise ValueError('The agent uses git.isolation = "shared"; a push would rewrite the shared '
                             'working tree. The agent fetches host commits from /run/host/git itself.')
        # A push to the branch checked out in /workspace also updates its work
        # tree. Git refuses the push while the work tree or index has changes,
        # or when it would overwrite an untracked file.
        command = ['git', '-c', 'receive.denyCurrentBranch=updateInstead', 'receive-pack']
    # stdout/stdin belong exclusively to Git's wire protocol. No shell, TTY,
    # root Git process, host-side checkout, or exposed TCP listener is involved.
    os.execvp('docker', ['docker', 'exec', '-i', '--user', 'node', container, *command, '/workspace'])


if __name__ == '__main__':
    try:
        if sys.argv[1:] == ['register']:
            register()
        elif len(sys.argv) == 2:
            serve(sys.argv[1])
        else:
            raise ValueError('usage: git-agent.py register|git-upload-pack|git-receive-pack')
    except (ValueError, OSError, KeyError, IndexError, subprocess.SubprocessError) as error:
        sys.exit(f'error: {error}')
