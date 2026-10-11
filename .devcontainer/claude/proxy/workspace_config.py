"""The workspace's proxy policy, from .devcontainer/proxy.yaml, and the host's CA.

proxy.yaml lists the requests to let through (routes), the request headers the
agent may send with them (headers), and the headers the proxy adds (inject),
whose values name secrets as ${NAME}. The secrets themselves stay on the host:
inject-credentials.py sends the named ones, from the host's credentials file
only, so the workspace decides where a secret goes but never sees it.

The image build installs proxy.yaml with `install`, so only a host build changes
it, and a bad file fails the build instead of the proxy. Anthropic's hosts stay
fixed in addon.py: their login is refreshed and their bodies are checked there.

The CA (CLAUDE_DEVCONTAINER_PROXY_CA) is the host's file, such as an in-house
root under /etc, mounted at runtime; compose mounts an empty file without one.
"""
import base64
from dataclasses import dataclass, field
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
# The hosts addon.py sends its own credentials to (its ROUTES; addon.py checks).
AUTHENTICATED_HOSTS = {'api.anthropic.com', 'mcp-proxy.anthropic.com'}
HEADER_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9-]*')
# Request headers that the agent may not pass: its own credentials (the proxy
# injects the host's), and what the proxy sets or that changes the connection.
UNPASSABLE_HEADERS = {'authorization', 'proxy-authorization', 'cookie', 'x-api-key', 'host', 'connection',
                      'upgrade', 'te', 'trailer', 'keep-alive', 'proxy-connection', 'content-length',
                      'transfer-encoding'}
# Headers that the proxy may not inject: they frame the request.
UNINJECTABLE_HEADERS = UNPASSABLE_HEADERS - {'authorization', 'proxy-authorization', 'cookie', 'x-api-key'}
SECRET = re.compile(r'\$\{([A-Z_][A-Z0-9_]*)\}')


@dataclass
class Config:
    # {host: [(method or None, path pattern)]}
    routes: dict = field(default_factory=dict)
    # {host: {lowercase names of the request headers to pass}}
    headers: dict = field(default_factory=dict)
    # {host: [(header name, template)]}; see parse_template.
    inject: dict = field(default_factory=dict)

    def secrets(self):
        """The names of the secrets that inject refers to."""
        return sorted({name for entries in self.inject.values() for _, (_, parts) in entries
                       for kind, name in parts if kind == 'secret'})


def parse_template(value, where):
    """Parse "... ${NAME} ..." or a mapping basic: "user:${NAME}" into (basic, [(literal|secret, text)])."""
    basic = isinstance(value, dict)
    if basic:
        if set(value) != {'basic'}:
            raise ValueError(f'{where}: expected a string, or "basic:" with "user:password"')
        value = value['basic']
    if not isinstance(value, str) or not value:
        raise ValueError(f'{where}: expected a non-empty string')
    parts, end = [], 0
    for match in SECRET.finditer(value):
        parts += [('literal', value[end:match.start()]), ('secret', match.group(1))]
        end = match.end()
    parts.append(('literal', value[end:]))
    parts = [(kind, text) for kind, text in parts if text]
    if any(kind == 'literal' and '$' in text for kind, text in parts):
        raise ValueError(f'{where}: "$" is only allowed in ${{NAME}}, NAME in capitals, digits and _')
    if any(kind == 'literal' and re.search(r'[\x00-\x1f\x7f]', text) for kind, text in parts):
        raise ValueError(f'{where}: control characters are not allowed')
    return basic, parts


def render(template, secrets):
    """The header value; KeyError names a secret the host has not sent."""
    basic, parts = template
    value = ''.join(secrets[text] if kind == 'secret' else text for kind, text in parts)
    return 'Basic ' + base64.b64encode(value.encode()).decode() if basic else value


def parse_route(route, where):
    """"METHOD path" or a bare path (any method) into (method or None, path pattern)."""
    if not isinstance(route, str):
        raise ValueError(f'{where}: expected "METHOD path" or a path')
    method, _, pattern = route.partition(' ') if not route.startswith('/') else (None, '', route)
    pattern = pattern.strip()
    if method is not None and not METHOD.fullmatch(method):
        raise ValueError(f'{where}: the method must be in capitals, as GET')
    if not pattern.startswith('/'):
        raise ValueError(f'{where}: the path must be a regular expression starting with "/"')
    try:
        re.compile(pattern)
    except re.error as error:
        raise ValueError(f'{where}: path: {error}') from None
    return method, pattern


def parse(text, source='proxy.yaml'):
    """Return the Config; raise ValueError if invalid."""
    try:
        # The safe loader builds only plain data, and rejects duplicate keys.
        data = YAML(typ='safe', pure=True).load(text)
    except YAMLError as error:
        raise ValueError(f'{source}: {error}') from None
    if data is None:
        data = {}
    hosts = data.get('hosts') if isinstance(data, dict) else None
    if not isinstance(data, dict) or not set(data) <= {'hosts'} or not isinstance({} if hosts is None else hosts, dict):
        raise ValueError(f'{source}: expected "hosts", a mapping of host names')
    config = Config()
    for host, settings in (hosts or {}).items():
        where = f'{source}: {host}'
        if not isinstance(host, str) or not HOST.fullmatch(host):
            raise ValueError(f'{where}: a host must be a lowercase DNS name')
        if host in AUTHENTICATED_HOSTS:
            raise ValueError(f'{where}: authenticated by the proxy itself; its routes are fixed in addon.py')
        if not isinstance(settings, dict) or not set(settings) <= {'routes', 'headers', 'inject'}:
            raise ValueError(f'{where}: expected "routes", "headers" and "inject"')
        routes, names, injected = settings.get('routes'), settings.get('headers', []), settings.get('inject', {})
        if not isinstance(routes, list) or not routes:
            raise ValueError(f'{where}: "routes" must list the requests to let through')
        config.routes[host] = [parse_route(route, f'{where}: routes[{index}]') for index, route in enumerate(routes)]
        if not isinstance(names, list) or not all(isinstance(name, str) and HEADER_NAME.fullmatch(name) for name in names):
            raise ValueError(f'{where}: "headers" must list header names')
        refused = {name.lower() for name in names} & UNPASSABLE_HEADERS
        if refused:
            raise ValueError(f'{where}: {", ".join(sorted(refused))} cannot be passed; use inject')
        if names:
            config.headers[host] = {name.lower() for name in names}
        if not isinstance(injected, dict):
            raise ValueError(f'{where}: "inject" must map header names to values')
        for name, value in injected.items():
            if not isinstance(name, str) or not HEADER_NAME.fullmatch(name) or name.lower() in UNINJECTABLE_HEADERS:
                raise ValueError(f'{where}: {name!r} cannot be injected')
            config.inject.setdefault(host, []).append((name, parse_template(value, f'{where}: inject: {name}')))
    return config


def installed(path=CONFIG):
    """The installed Config; empty without a proxy.yaml."""
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return Config()
    return parse(text)


def install(source, target=DIR):
    """Check source/proxy.yaml and install it in target."""
    try:
        text = (Path(source) / 'proxy.yaml').read_text()
    except FileNotFoundError:
        return
    config = parse(text)
    Path(target).mkdir(parents=True, exist_ok=True)
    (Path(target) / CONFIG.name).write_text(text)
    for host, entries in config.routes.items():
        for method, pattern in entries:
            print(f'workspace route: {method or "*"} {host} {pattern}')
    for host, names in config.headers.items():
        print(f'workspace headers: {host} {" ".join(sorted(names))}')
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
    # install <dir>: the image build's check. secrets: the names that
    # inject-credentials.py asks for, one per line.
    try:
        if len(sys.argv) == 3 and sys.argv[1] == 'install':
            install(sys.argv[2])
        elif sys.argv[1:] == ['secrets']:
            print('\n'.join(installed().secrets()))
        else:
            sys.exit('usage: workspace_config.py install <.devcontainer directory> | secrets')
    except ValueError as error:
        sys.exit(f'error: {error}')
