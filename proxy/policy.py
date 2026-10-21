"""The workspace's proxy policy: proxy.hosts of .devcontainer/claude.toml.

Each host lists the requests to let through (routes), the request headers the
agent may send with them (headers), and the headers the proxy adds (inject),
whose values name secrets as ${NAME}. The secrets themselves stay on the host:
inject-credentials.py sends the named ones, from the host's credentials file
only, so the workspace decides where a secret goes but never sees it. http =
true lets the routes through over plain HTTP on port 80 too, besides HTTPS on
443; such a host takes no inject, so that no secret crosses the network in the
clear.

Only the standard library: the host's scripts/settings.py checks the same hosts
with this file before the image build does.
"""
import base64
from dataclasses import dataclass, field
import re

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
FIELDS = {'routes', 'headers', 'inject', 'http'}


@dataclass
class Config:
    # {host: [(method or None, path pattern)]}
    routes: dict = field(default_factory=dict)
    # {host: {lowercase names of the request headers to pass}}
    headers: dict = field(default_factory=dict)
    # {host: [(header name, template)]}; see parse_template.
    inject: dict = field(default_factory=dict)
    # {hosts also reached over plain HTTP on port 80}
    http: set = field(default_factory=set)

    def secrets(self):
        """The names of the secrets that inject refers to."""
        return sorted({name for entries in self.inject.values() for _, (_, value) in entries
                       for name in SECRET.findall(value)})


def parse_template(value, where):
    """Validate "... ${NAME} ..." or {basic = "user:${NAME}"} and return (basic, text)."""
    basic = isinstance(value, dict)
    if basic:
        if set(value) != {'basic'}:
            raise ValueError(f'{where}: expected a string, or {{ basic = "user:password" }}')
        value = value['basic']
    if not isinstance(value, str) or not value:
        raise ValueError(f'{where}: expected a non-empty string')
    if '$' in SECRET.sub('', value):
        raise ValueError(f'{where}: "$" is only allowed in ${{NAME}}, NAME in capitals, digits and _')
    if re.search(r'[\x00-\x1f\x7f]', value):
        raise ValueError(f'{where}: control characters are not allowed')
    return basic, value


def render(template, secrets):
    """The header value; KeyError names a secret the host has not sent."""
    basic, text = template
    value = SECRET.sub(lambda match: secrets[match.group(1)], text)
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


def parse(hosts, source='proxy.hosts'):
    """Return the Config of {host: {routes, headers, inject, http}}; raise ValueError if invalid."""
    if not isinstance({} if hosts is None else hosts, dict):
        raise ValueError(f'{source}: expected a table of host names')
    config = Config()
    for host, settings in (hosts or {}).items():
        where = f'{source}."{host}"'
        if not isinstance(host, str) or not HOST.fullmatch(host):
            raise ValueError(f'{where}: a host must be a lowercase DNS name')
        if host in AUTHENTICATED_HOSTS:
            raise ValueError(f'{where}: authenticated by the proxy itself; its routes are fixed in addon.py')
        if not isinstance(settings, dict) or not set(settings) <= FIELDS:
            raise ValueError(f'{where}: expected "routes", "headers", "inject" and "http"')
        routes, names, injected = settings.get('routes'), settings.get('headers', []), settings.get('inject', {})
        if not isinstance(routes, list) or not routes:
            raise ValueError(f'{where}.routes: must list the requests to let through')
        config.routes[host] = [parse_route(route, f'{where}.routes[{index}]') for index, route in enumerate(routes)]
        if not isinstance(names, list) or not all(isinstance(name, str) and HEADER_NAME.fullmatch(name) for name in names):
            raise ValueError(f'{where}.headers: must list header names')
        refused = {name.lower() for name in names} & UNPASSABLE_HEADERS
        if refused:
            raise ValueError(f'{where}.headers: {", ".join(sorted(refused))} cannot be passed; use inject')
        if names:
            config.headers[host] = {name.lower() for name in names}
        if not isinstance(injected, dict):
            raise ValueError(f'{where}.inject: must map header names to values')
        for name, value in injected.items():
            if not isinstance(name, str) or not HEADER_NAME.fullmatch(name) or name.lower() in UNINJECTABLE_HEADERS:
                raise ValueError(f'{where}.inject: {name!r} cannot be injected')
            config.inject.setdefault(host, []).append((name, parse_template(value, f'{where}.inject.{name}')))
        plain = settings.get('http', False)
        if not isinstance(plain, bool):
            raise ValueError(f'{where}.http: expected true or false')
        if plain and injected:
            raise ValueError(f'{where}.http: plain HTTP would send inject\'s secrets in the clear')
        if plain:
            config.http.add(host)
    return config
