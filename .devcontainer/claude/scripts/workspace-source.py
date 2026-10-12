#!/usr/bin/env python3
"""Write credential-free inputs for the agent's first remote clone."""
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[3]


def https_origin(value):
    if value.startswith('git@github.com:'):
        value = 'https://github.com/' + value.removeprefix('git@github.com:')
    if value.startswith('ssh://git@github.com/'):
        value = 'https://github.com/' + value.removeprefix('ssh://git@github.com/')
    url = urlsplit(value)
    if (url.scheme != 'https' or not url.hostname or not url.path.strip('/')
            or '?' in value or '#' in value or any(c.isspace() or ord(c) < 32 for c in value)):
        raise ValueError('Set origin to an HTTPS repository URL without a query or fragment.')
    return urlunsplit(('https', url.netloc.rsplit('@', 1)[-1], url.path, '', ''))


def write(root=ROOT):
    root = Path(root)
    def git(*args):
        return subprocess.run(['git', '-C', str(root), *args], check=True,
                              capture_output=True, text=True).stdout.strip()
    try:
        # Read the configured URL, not a URL rewritten through insteadOf.
        url = https_origin(git('config', '--get', 'remote.origin.url'))
        commit = git('rev-parse', '--verify', 'HEAD^{commit}')
    except subprocess.CalledProcessError:
        raise ValueError('A committed HEAD and an origin remote are required.') from None
    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', commit):
        raise ValueError('HEAD did not resolve to a full commit ID.')
    data = {'url': url, 'commit': commit}
    directory = root / '.devcontainer/.generated'
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / 'workspace.json.tmp'
    temporary.write_text(json.dumps(data) + '\n')
    temporary.replace(directory / 'workspace.json')
    return data


if __name__ == '__main__':
    try:
        write()
    except ValueError as error:
        sys.exit(f'error: {error}')
