"""The workspace's extra routes, from .devcontainer/proxy-routes.json.

The image build copies the file in and runs this module to check it, so only a
host build changes them, and a bad file fails the build instead of the proxy.
These routes get no credentials: they are for public hosts such as
fonts.googleapis.com. Hosts that the proxy authenticates to stay fixed in
addon.py, so the workspace cannot route a credential somewhere new.
"""
import json
from pathlib import Path
import re
import sys

PATH = Path('/opt/proxy/workspace/proxy-routes.json')
HOST = re.compile(r'(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}')
METHOD = re.compile(r'[A-Z]+')
# The hosts addon.py sends credentials to (its ROUTES; addon_test.py checks).
AUTHENTICATED_HOSTS = {'api.github.com', 'github.com', 'api.anthropic.com'}


def load(path=PATH):
    """Return {host: [(method or None, path pattern)]}; raise ValueError if the file is invalid."""
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return {}
    data = json.loads(text, object_pairs_hook=_unique)
    if not isinstance(data, dict) or set(data) != {'routes'} or not isinstance(data['routes'], list):
        raise ValueError(f'{path}: expected {{"routes": [...]}}')
    routes = {}
    for index, route in enumerate(data['routes']):
        where = f'{path}: routes[{index}]'
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


def _unique(pairs):
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError('duplicate key in proxy-routes.json')
    return dict(pairs)


if __name__ == '__main__':
    # The image build's check.
    try:
        for host, routes in load(*sys.argv[1:]).items():
            for method, pattern in routes:
                print(f'workspace route: {method or "*"} {host} {pattern}')
    except ValueError as error:
        sys.exit(f'error: {error}')
