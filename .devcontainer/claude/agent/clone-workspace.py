#!/usr/bin/env python3
"""Initialize the agent's Git once, without replacing user work.

In clone mode /workspace is an empty volume that receives the host's
committed HEAD. In shared mode /workspace is the host's working tree and
only /workspace/.git, the agent's own volume, is built, from the same local
snapshot; the files already there are never written.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from workspace_mounts import HOST_GIT, dependency_mounts


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


def fetch_snapshot(git, source, data, marker):
    """Fetch the starting commit from the host's snapshot; return the inputs used.

    The host keeps only its latest snapshot (workspace-source.py). A retry
    whose snapshot is gone before its fetch, when nothing has been checked
    out yet, starts over from the current one.
    """
    if git('cat-file', '-e', data['commit'] + '^{commit}', check=False).returncode == 0:
        return data
    directory = Path(source).absolute().parent
    if not (directory / data['seed']).exists():
        data = inputs(source)
        marker.write_text(json.dumps(data) + '\n')
    seed = directory / data['seed']
    try:
        git('-c', f'safe.directory={seed}', 'fetch', '--no-tags', '--no-recurse-submodules', '--update-shallow',
            str(seed), 'refs/heads/bootstrap', timeout=300)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise ValueError('Could not import the local Git snapshot. Run workspace-source.py on the host, '
                         'then run wait-ready.sh as node again.') from None
    if git('cat-file', '-e', data['commit'] + '^{commit}', check=False).returncode:
        raise ValueError('The local Git snapshot lacks the starting commit; inspect the host snapshot.')
    return data


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
    data = fetch_snapshot(git, source, data, marker)
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


def add_host_remote(workspace, host_git=HOST_GIT):
    """Name the host's read-only Git `host`, once; a remote of that name already there stays."""
    if not Path(host_git).is_dir():
        return
    remotes = subprocess.run(['git', '-C', str(workspace), 'remote'], check=True, capture_output=True, text=True)
    if 'host' not in remotes.stdout.split():
        subprocess.run(['git', '-C', str(workspace), 'remote', 'add', 'host', str(host_git)],
                       check=True, capture_output=True, text=True)


def attach(source, workspace, host_git=HOST_GIT):
    """Build the agent's Git for a shared working tree; index from HEAD, files untouched."""
    workspace = Path(workspace).absolute()
    git_dir = workspace / '.git'
    marker = git_dir / 'claude-bootstrap.json'
    hidden = Path(source).absolute().parent / 'hidden.txt'
    environment = dict(os.environ, GIT_TERMINAL_PROMPT='0')

    def git(*args, check=True, timeout=30, input=None):
        return subprocess.run(['git', '-C', str(workspace), *args],
                              env=environment, check=check, timeout=timeout, input=input,
                              capture_output=True, text=True)

    if marker.is_file():
        data = inputs(marker)
    elif (git_dir / 'HEAD').exists():
        if git('rev-parse', '--verify', 'HEAD^{commit}', check=False).returncode == 0:
            follow(git, host_git)
            skip_hidden(git, hidden)
            return
        raise ValueError('Incomplete repository without initialization metadata; inspect /workspace/.git.')
    else:
        data = inputs(source)
        marker.write_text(json.dumps(data) + '\n')

    git('init', '--object-format=' + ('sha256' if len(data['commit']) == 64 else 'sha1'))
    data = fetch_snapshot(git, source, data, marker)
    branch = data.get('branch', '')
    if branch:
        existing = git('show-ref', '--verify', '--hash', 'refs/heads/' + branch, check=False)
        if existing.returncode == 0 and existing.stdout.strip() != data['commit']:
            raise ValueError('Starting branch changed during initialization; inspect /workspace/.git before retrying.')
        git('update-ref', 'refs/heads/' + branch, data['commit'])
        git('symbolic-ref', 'HEAD', 'refs/heads/' + branch)
    else:
        git('update-ref', '--no-deref', 'HEAD', data['commit'])
    # The index from HEAD, refreshed against the host's files as they are.
    git('reset', '-q', '--mixed')
    skip_hidden(git, hidden)
    marker.unlink()


def skip_hidden(git, hidden):
    """Keep the masked paths out of status and commits.

    The host masks them with empty mounts, so to this Git their tracked
    content looks emptied or deleted. skip-worktree, unlike assume-unchanged,
    also makes `git add` refuse them.
    """
    try:
        paths = [line for line in hidden.read_text().splitlines() if line]
    except FileNotFoundError:
        return
    tracked = git('ls-files', '-z', '--', *paths).stdout if paths else ''
    if tracked:
        git('update-index', '-z', '--skip-worktree', '--stdin', input=tracked)


def follow(git, host_git=HOST_GIT):
    """Move HEAD and the index to the host's current branch when no agent work is pending.

    The files are always the host's; a HEAD left behind the host's commits
    would show them all as uncommitted changes.
    """
    def warn(message):
        print(f'warning: {message} This Git stays where it is.', file=sys.stderr)

    head = git('rev-parse', '--verify', 'HEAD^{commit}').stdout.strip()
    host = subprocess.run(['git', '--git-dir', str(host_git), 'symbolic-ref', '--quiet', 'HEAD'],
                          capture_output=True, text=True)
    if host.returncode not in (0, 1):
        return warn(f'Could not read the host branch from {host_git}.')
    ref = host.stdout.strip() if host.returncode == 0 else 'HEAD'
    if ref != 'HEAD' and not ref.startswith('refs/heads/'):
        return warn(f'The host HEAD references {ref}, not a branch.')
    fetched = git('fetch', '--quiet', '--no-tags', '--no-recurse-submodules', str(host_git), ref,
                  check=False, timeout=300)
    if fetched.returncode:
        return warn(f'Could not fetch the host commit from {host_git}: {fetched.stderr.strip()}.')
    commit = git('rev-parse', '--verify', 'FETCH_HEAD^{commit}').stdout.strip()
    current = git('symbolic-ref', '--quiet', 'HEAD', check=False)
    current_ref = current.stdout.strip() if current.returncode == 0 else 'HEAD'
    if (commit, ref) == (head, current_ref):
        return
    if git('merge-base', '--is-ancestor', head, commit, check=False).returncode:
        return warn(f'The host is at {commit[:12]} on {ref}, which does not contain this HEAD {head[:12]}; '
                    f'commits here are not accepted yet. Integrate with git fetch {host_git} {ref} and a rebase.')
    if git('diff', '--cached', '--quiet', check=False).returncode:
        return warn(f'The host moved to {commit[:12]} on {ref}, but the index has staged changes; '
                    f'commit or unstage them, then git fetch {host_git} {ref} and git reset --mixed FETCH_HEAD.')
    if ref == 'HEAD':
        git('update-ref', '--no-deref', 'HEAD', commit)
    else:
        git('update-ref', ref, commit)
        git('symbolic-ref', 'HEAD', ref)
    git('reset', '-q', '--mixed')


if __name__ == '__main__':
    if os.geteuid() == 0:
        sys.exit('error: run workspace initialization as node, never root.')
    try:
        if os.environ.get('CLAUDE_DEVCONTAINER_GIT_ISOLATION') == 'unsafe_shared':
            pass  # Host Git is already initialized; never reset its shared index.
        elif os.environ.get('CLAUDE_DEVCONTAINER_GIT_ISOLATION', 'clone') == 'shared':
            attach('/run/host/generated/workspace.json', '/workspace')
        else:
            clone('/run/host/generated/workspace.json', '/workspace')
            add_host_remote('/workspace')
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        sys.exit(f'error: workspace initialization failed: {error}')
