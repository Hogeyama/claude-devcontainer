#!/usr/bin/env python3
"""Export the local starting commit for the agent's first independent clone,
and the host's remotes that the agent can fetch from (remotes.json)."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import layout  # noqa: E402

# The workspace root (scripts/layout.py).
ROOT = layout.workspace_root()
# The agent's own remotes, which the host's do not replace.
RESERVED_REMOTES = {'host', 'agent'}
REMOTE_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')
# scp-like SSH, [user@]host:path.
SCP = re.compile(r'(?:[^@/:]+@)?([A-Za-z0-9.-]+):(?!/)(.+)')


def https_url(url):
    """url as HTTPS on port 443 without credentials, which the proxy can carry; or None.

    The agent reaches only the proxy, so SSH remotes become HTTPS to the same
    path, as GitHub and most forges serve both.
    """
    if '://' not in url:
        match = SCP.fullmatch(url)
        if not match:
            return None
        host, path = match.group(1), '/' + match.group(2)
    else:
        parts = urlsplit(url)
        if parts.scheme not in ('https', 'ssh') or parts.query or parts.fragment:
            return None
        try:
            port = parts.port
        except ValueError:
            return None
        if parts.scheme == 'https' and port not in (None, 443):
            return None
        host, path = parts.hostname, parts.path
    if not host or not path.strip('/') or re.search(r'[\s\x00-\x1f\x7f]', path):
        return None
    return f'https://{host.lower()}{path}'


def remotes(root, hosts):
    """{name: URL} of the host's remotes that reach one of hosts, as HTTPS without credentials."""
    result = subprocess.run(['git', '-C', str(root), 'remote'], check=True, capture_output=True, text=True)
    found = {}
    for name in result.stdout.split():
        if name in RESERVED_REMOTES or not REMOTE_NAME.fullmatch(name):
            continue
        # get-url applies url.*.insteadOf, as the host's own fetch would.
        url = subprocess.run(['git', '-C', str(root), 'remote', 'get-url', name],
                             capture_output=True, text=True).stdout.strip()
        converted = https_url(url)
        if converted and urlsplit(converted).hostname in hosts:
            found[name] = converted
    return found


def proxy_hosts(root):
    """The hosts of proxy.hosts, or none without git.inherit_remotes."""
    spec = importlib.util.spec_from_file_location('settings', Path(__file__).resolve().parent / 'settings.py')
    settings = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(settings)
    loaded = settings.load(root)
    return set(loaded['proxy']['hosts']) if loaded['git']['inherit_remotes'] else set()


def write(root=ROOT, hosts=None):
    root = Path(root)
    hosts = proxy_hosts(root) if hosts is None else hosts
    def git(*args):
        return subprocess.run(['git', '-C', str(root), *args], check=True,
                              capture_output=True, text=True).stdout.strip()
    try:
        commit = git('rev-parse', '--verify', 'HEAD^{commit}')
    except subprocess.CalledProcessError:
        raise ValueError('A committed HEAD is required.') from None
    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', commit):
        raise ValueError('HEAD did not resolve to a full commit ID.')
    branch = subprocess.run(['git', '-C', str(root), 'symbolic-ref', '--quiet', 'HEAD'],
                            capture_output=True, text=True)
    if branch.returncode not in (0, 1):
        raise ValueError('Could not read the host branch.')
    reference = branch.stdout.strip()
    if branch.returncode == 0 and not reference.startswith('refs/heads/'):
        raise ValueError('Host HEAD does not reference a branch.')
    data = {'commit': commit, 'seed': f'workspace-{commit}.git',
            'branch': reference.removeprefix('refs/heads/') if branch.returncode == 0 else ''}
    directory = layout.config_dir(root) / '.generated'
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
    temporary = directory / 'remotes.json.tmp'
    temporary.write_text(json.dumps(remotes(root, hosts)) + '\n')
    temporary.replace(directory / 'remotes.json')
    # Each is a full copy of the history, so keep only the current one. An
    # agent whose initialization was interrupted before its fetch starts over
    # from it (agent/clone-workspace.py); a finished one needs none.
    for old in directory.glob('workspace-*.git'):
        if old != seed:
            shutil.rmtree(old)
    return data


if __name__ == '__main__':
    try:
        write()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        sys.exit(f'error: {error}')
