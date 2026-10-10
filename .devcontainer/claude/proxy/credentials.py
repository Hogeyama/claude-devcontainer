"""Credentials injected at runtime, kept in a 0400 file on the proxy's private tmpfs."""
import json
import os
from pathlib import Path
import sys
import tempfile

SNAPSHOT = Path('/run/proxy-credentials/credentials.json')
KEYS = {'github_token', 'anthropic_token', 'auth_mode'}


def parse(raw):
    value = json.loads(raw)
    if not (isinstance(value, dict) and set(value) == KEYS
            and all(isinstance(value[key], str) and value[key] for key in KEYS)
            and value['auth_mode'] in ('api-key', 'oauth')):
        raise ValueError('expected {"github_token", "anthropic_token", "auth_mode": "api-key" | "oauth"}')
    return value['github_token'], value['anthropic_token'], value['auth_mode']


def read_snapshot(path=SNAPSHOT):
    return parse(path.read_bytes())


def install_snapshot(raw, path=SNAPSHOT):
    parse(raw)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        output.write(raw)
    os.chmod(output.name, 0o400)
    os.replace(output.name, path)


if __name__ == '__main__':
    if sys.argv[1:] != ['inject']:
        sys.exit('usage: credentials.py inject < credentials.json')
    try:
        install_snapshot(sys.stdin.buffer.read())
    except ValueError as error:
        sys.exit(f'error: invalid credentials: {error}')
