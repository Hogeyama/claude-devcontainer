"""The workspace's routes, from .devcontainer/proxy.yaml, and the host's CA.

The image build installs proxy.yaml with `install`, so only a host build changes
the routes, and a bad file fails the build instead of the proxy. The routes get
no credentials: they are for public hosts such as fonts.googleapis.com. Hosts
that the proxy authenticates to stay fixed in addon.py, so the workspace cannot
route a credential somewhere new.

The CA (CLAUDE_DEVCONTAINER_PROXY_CA) is the host's file, such as an in-house
root under /etc, mounted at runtime; compose mounts an empty file without one.
"""
from pathlib import Path
import re
import sys

import certifi
from cryptography import x509
from ruamel.yaml import YAML, YAMLError

DIR = Path('/opt/proxy/workspace')
CONFIG = DIR / 'proxy.yaml'
CA = Path('/run/proxy-ca/ca.pem')
# certifi's roots plus the host's CA, for upstream TLS.
UPSTREAM_CA = Path('/tmp/proxy/upstream-ca.pem')
HOST = re.compile(r'(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}')
METHOD = re.compile(r'[A-Z]+')
# The hosts addon.py sends credentials to (its ROUTES; addon.py checks).
AUTHENTICATED_HOSTS = {'api.github.com', 'github.com', 'api.anthropic.com', 'mcp-proxy.anthropic.com'}


def parse(text, source='proxy.yaml'):
    """Return the routes as {host: [(method or None, path pattern)]}; raise ValueError if invalid."""
    try:
        # The safe loader builds only plain data, and rejects duplicate keys.
        data = YAML(typ='safe', pure=True).load(text)
    except YAMLError as error:
        raise ValueError(f'{source}: {error}') from None
    if data is None:
        data = {}
    if not isinstance(data, dict) or not set(data) <= {'routes'}:
        raise ValueError(f'{source}: expected a mapping with "routes"')
    entries = data.get('routes', [])
    if not isinstance(entries, list):
        raise ValueError(f'{source}: "routes" must be a list')
    routes = {}
    for index, route in enumerate(entries):
        where = f'{source}: routes[{index}]'
        if not isinstance(route, dict) or set(route) != {'method', 'host', 'path'}:
            raise ValueError(f'{where}: expected exactly "method", "host" and "path"')
        method, host, pattern = route['method'], route['host'], route['path']
        if not isinstance(host, str) or not HOST.fullmatch(host):
            raise ValueError(f'{where}: host must be a lowercase DNS name')
        if host in AUTHENTICATED_HOSTS:
            raise ValueError(f'{where}: {host} is authenticated by the proxy; its routes are fixed in addon.py')
        if not isinstance(method, str) or not (method == '*' or METHOD.fullmatch(method)):
            raise ValueError(f'{where}: method must be an uppercase HTTP method or "*"')
        if not isinstance(pattern, str) or not pattern.startswith('/'):
            raise ValueError(f'{where}: path must be a regular expression starting with "/"')
        try:
            re.compile(pattern)
        except re.error as error:
            raise ValueError(f'{where}: path: {error}') from None
        routes.setdefault(host, []).append((None if method == '*' else method, pattern))
    return routes


def routes(path=CONFIG):
    """The installed routes; none without a proxy.yaml."""
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return {}
    return parse(text)


def install(source, target=DIR):
    """Check source/proxy.yaml and install it in target."""
    try:
        text = (Path(source) / 'proxy.yaml').read_text()
    except FileNotFoundError:
        return
    found = parse(text)
    Path(target).mkdir(parents=True, exist_ok=True)
    (Path(target) / CONFIG.name).write_text(text)
    for host, entries in found.items():
        for method, pattern in entries:
            print(f'workspace route: {method or "*"} {host} {pattern}')


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
        raise ValueError('CLAUDE_DEVCONTAINER_PROXY_CA: expected PEM certificates') from None
    for certificate in certificates:
        try:
            is_ca = certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
        except x509.ExtensionNotFound:
            is_ca = False
        if not is_ca:
            raise ValueError(f'CLAUDE_DEVCONTAINER_PROXY_CA: {certificate.subject.rfc4514_string()} is not a CA certificate')
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(Path(certifi.where()).read_text() + '\n' + data.decode())
    return target


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] != 'install':
        sys.exit('usage: workspace_config.py install <.devcontainer directory>')
    try:
        install(sys.argv[2])
    except ValueError as error:
        sys.exit(f'error: {error}')
