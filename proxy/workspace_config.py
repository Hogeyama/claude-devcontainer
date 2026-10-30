"""The workspace's proxy policy, installed in the image, and the host's CA.

The policy is proxy.hosts of .devcontainer/claude.toml and claude.local.toml,
which the host's scripts/settings.py merges and writes to
.devcontainer/.generated/proxy.json; policy.py describes and checks it. The
image build installs that file with `install`, so only a host build changes
it, and a bad file fails the build instead of the proxy. Anthropic's hosts stay
fixed in addon.py: their login is refreshed and their bodies are checked there.

The CA (proxy.ca in claude.toml) is the host's file, such as an in-house root
under /etc, mounted at runtime; compose mounts an empty file without one.
"""
import json
from pathlib import Path
import sys

import certifi
from cryptography import x509

import policy

DIR = Path('/opt/proxy/workspace')
FILE = 'proxy.json'
CA = Path('/run/proxy-ca/ca.pem')
# certifi's roots plus the host's CA, for upstream TLS.
UPSTREAM_CA = Path('/tmp/proxy/upstream-ca.pem')


def parse(text, source=FILE):
    """The Config of proxy.json's text, {"hosts": {...}}; raise ValueError if invalid."""
    try:
        data = json.loads(text)
    except ValueError as error:
        raise ValueError(f'{source}: {error}') from None
    if not isinstance(data, dict) or not set(data) <= {'hosts'}:
        raise ValueError(f'{source}: expected {{"hosts": {{...}}}}')
    return policy.parse(data.get('hosts'), f'{source}: proxy.hosts')


def installed(directory=DIR):
    """The installed Config."""
    return parse((Path(directory) / FILE).read_text())


def install(source, target=DIR):
    """Check the proxy.json at source and install it in target."""
    text = Path(source).read_text()
    config = parse(text)
    Path(target).mkdir(parents=True, exist_ok=True)
    (Path(target) / FILE).write_text(text)
    for host, entries in config.routes.items():
        for method, pattern in entries:
            print(f'workspace route: {method or "*"} {host} {pattern}')
    for host, names in config.headers.items():
        print(f'workspace headers: {host} {" ".join(sorted(names))}')
    for host in sorted(config.http):
        print(f'workspace plain http: {host}')
    for host, entries in config.inject.items():
        print(f'workspace inject: {host} {" ".join(name for name, _ in entries)}')
    if config.secrets():
        print(f'workspace secrets: {" ".join(config.secrets())}')


def upstream_ca(ca=CA, target=UPSTREAM_CA):
    """Bundle the host's CA with certifi's roots in target and return it; None without a CA."""
    try:
        data = Path(ca).read_bytes()
    except FileNotFoundError:
        return None  # Run without compose's mount.
    if not data.strip():
        return None
    try:
        certificates = x509.load_pem_x509_certificates(data)
    except ValueError:
        raise ValueError('proxy.ca: expected PEM certificates') from None
    for certificate in certificates:
        try:
            is_ca = certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
        except x509.ExtensionNotFound:
            is_ca = False
        if not is_ca:
            raise ValueError(f'proxy.ca: {certificate.subject.rfc4514_string()} is not a CA certificate')
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(Path(certifi.where()).read_text() + '\n' + data.decode())
    return target


if __name__ == '__main__':
    # install <proxy.json>: the image build's check. secrets: the names that
    # inject-credentials.py asks for, one per line.
    try:
        if len(sys.argv) == 3 and sys.argv[1] == 'install':
            install(sys.argv[2])
        elif sys.argv[1:] == ['secrets']:
            print('\n'.join(installed().secrets()))
        else:
            sys.exit('usage: workspace_config.py install <proxy.json> | secrets')
    except ValueError as error:
        sys.exit(f'error: {error}')
