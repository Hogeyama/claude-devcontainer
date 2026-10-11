"""Credentials injected at runtime, kept in a 0400 file on the proxy's private tmpfs."""
import json
import os
from pathlib import Path
import re
import sys
import tempfile

SNAPSHOT = Path('/run/proxy-credentials/credentials.json')
KEYS = {'anthropic_token', 'auth_mode'}
# Optional: {NAME: value} for the ${NAME}s in the workspace's proxy.yaml.
SECRETS = 'secrets'
SECRET_NAME = re.compile(r'[A-Z_][A-Z0-9_]*')


def parse(raw):
    value = json.loads(raw)
    if not (isinstance(value, dict) and KEYS <= set(value) <= KEYS | {SECRETS}
            and all(isinstance(value[key], str) and value[key] for key in KEYS)
            and value['auth_mode'] in ('api-key', 'oauth')):
        raise ValueError('expected {"anthropic_token", "auth_mode": "api-key" | "oauth", "secrets"?: {NAME: value}}')
    secrets = value.get(SECRETS, {})
    if not (isinstance(secrets, dict) and all(SECRET_NAME.fullmatch(name) and isinstance(secret, str)
                                              and not re.search(r'[\x00-\x1f\x7f]', secret)
                                              for name, secret in secrets.items())):
        raise ValueError('"secrets" must map NAMEs to strings without control characters')
    return (value['anthropic_token'], value['auth_mode']), secrets


def read_snapshot(path=SNAPSHOT):
    return parse(path.read_bytes())[0]


def read_secrets(path=SNAPSHOT):
    return parse(path.read_bytes())[1]


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
