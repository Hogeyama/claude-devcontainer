#!/usr/bin/env python3
"""The workspace's settings: .devcontainer/claude.toml, and this host's claude.local.toml.

claude.toml is committed with the workspace; claude.local.toml, beside it and
not committed, has the same form and overrides it. Tables merge key by key;
other values, lists included, replace the shared ones, and each host under
proxy.hosts replaces the same host as a whole. Every setting may be left out
(DEFAULTS). Unknown settings and values of the wrong type are errors, so that a
misspelt setting is not silently ignored.

compose reads only environment variables and the proxy takes its policy at
image build, so `generate` (initializeCommand, and compose.sh) writes what
they read to .devcontainer/.generated/:
  settings.env  the variables compose's files interpolate.
  proxy.json    the merged proxy.hosts, which the proxy image build installs.
  images.txt    docker.images, one per line, for load-images.py and wait-ready.sh.
  agent.env     the dummy tokens that proxy.hosts' env names, for the agent.

The settings come from these files only. A variable of settings.env in the
environment or in .devcontainer/.env would win over settings.env in compose
alone, so `generate` refuses to run with one.

Usage, from anywhere:
  settings.py generate         check the settings and write .generated/
  settings.py prepare-dependencies  create persistent Node overlay directories
  settings.py proxy-json FILE  print proxy.json for one claude.toml, as for a
                               test build of the proxy image
"""
import sys

if sys.version_info < (3, 11):
    sys.exit('error: claude-devcontainer needs Python 3.11 or later on the host, '
             f'for tomllib; this is {sys.version.split()[0]}.')

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import tomllib

# The workspace root: this script is .devcontainer/claude/scripts/ in it.
ROOT = Path(__file__).resolve().parents[3]
FILES = ('claude.toml', 'claude.local.toml')
CONFIG = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'claude-devcontainer'
# {table: {key: default}}; the defaults also give each setting's type.
DEFAULTS = {
    'agent': {'uid': 1000, 'gid': 1000},
    'docker': {'enabled': False, 'images': []},
    'nix': {'enabled': False, 'ref': 'HEAD', 'shell': ''},
    'sumi': {'enabled': False, 'secrets': str(CONFIG / 'sumi/secrets.txt')},
    'proxy': {'ca': '', 'connectors': [], 'hosts': {}},
    'subnets': {'proxy': '', 'dind': ''},
}
HOST_FIELDS = {'routes', 'headers', 'inject', 'env'}
# compose's names, which settings.env writes. Only these may not come from
# elsewhere; compose sets others, such as _PROXY_ADDRESS, in the containers.
PREFIX = 'CLAUDE_DEVCONTAINER_'
VARIABLES = tuple(PREFIX + name for name in (
    'UID', 'GID', 'PROXY_NET', 'DIND_NET', 'DIND', 'NIX', 'NIX_REF', 'NIX_SHELL', 'SUMI', 'SUMI_DIR', 'SUMI_FILE',
    'PROXY_CA', 'CONNECTORS', 'NODE_LOWER', 'NODE_UPPER', 'NODE_WORK'))
DUMMY = 'managed-by-proxy'
DOTENV_KEY = re.compile(r'\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_.-]*)\s*(?:[=:]|$)')
ENV_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
# What compose sets in the agent; a dummy may not replace it.
RESERVED_ENV = {'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'DOCKER_HOST', 'CLAUDE_CODE_OAUTH_TOKEN'}
RESERVED_ENV_PREFIXES = ('TESTCONTAINERS_', PREFIX)
CONNECTOR = re.compile(r'mcp(srv|rs)_[A-Za-z0-9]+')
SUBNET = re.compile(r'(\d{1,3})\.(\d{1,3})\.(\d{1,3})')
# rootlesskit in DinD maps these subordinate IDs besides the agent's UID.
SUBORDINATE_IDS = range(100000, 165536)


def _policy():
    """The proxy's policy.py, which checks proxy.hosts as the image build does."""
    spec = importlib.util.spec_from_file_location('policy', Path(__file__).resolve().parents[1] / 'proxy/policy.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


policy = _policy()


def type_name(default):
    return {bool: 'true or false', int: 'an integer', str: 'a string', list: 'a list of strings',
            dict: 'a table'}[type(default)]


def check_hosts(hosts, where):
    """Check proxy.hosts as the proxy does, besides each host's env."""
    for host, entry in hosts.items():
        at = f'{where}."{host}"'
        if not isinstance(entry, dict):
            raise ValueError(f'{at}: expected a table')
        unknown = sorted(set(entry) - HOST_FIELDS)
        if unknown:
            # [proxy.hosts.api.github.com] makes host "api" with a table "github".
            nested = any(isinstance(entry[key], dict) for key in unknown)
            raise ValueError(f'{at}: unknown {", ".join(unknown)}'
                             + ('. Quote host names, which contain dots, as [proxy.hosts."api.github.com"]'
                                if nested else ''))
        names = entry.get('env', [])
        if not isinstance(names, list) or not all(isinstance(name, str) and ENV_NAME.fullmatch(name) for name in names):
            raise ValueError(f'{at}.env: must list environment variable names')
        for name in names:
            if name.upper() in RESERVED_ENV or name.upper().startswith(RESERVED_ENV_PREFIXES):
                raise ValueError(f'{at}.env: {name} is set by compose and cannot be a dummy')
    policy.parse({host: without_env(entry) for host, entry in hosts.items()}, where)


def without_env(entry):
    return {key: value for key, value in entry.items() if key != 'env'}


def check(data, source):
    """Check the tables, keys and types of one file's data."""
    for table, values in data.items():
        if table not in DEFAULTS:
            raise ValueError(f'{source}: unknown table [{table}]')
        if not isinstance(values, dict):
            raise ValueError(f'{source}: {table}: expected a table')
        for key, value in values.items():
            where = f'{source}: {table}.{key}'
            if key not in DEFAULTS[table]:
                raise ValueError(f'{where}: unknown setting')
            default = DEFAULTS[table][key]
            # bool is an int in Python, but not a UID.
            if type(value) is not type(default) or (isinstance(value, list) and not all(isinstance(item, str) for item in value)):
                raise ValueError(f'{where}: expected {type_name(default)}')
            if isinstance(value, str) and ('\n' in value or "'" in value):
                raise ValueError(f"{where}: newlines and ' are not allowed")
    agent, docker, proxy, subnets = (data.get(table, {}) for table in ('agent', 'docker', 'proxy', 'subnets'))
    for key in ('uid', 'gid'):
        if agent.get(key, 1) <= 0:
            raise ValueError(f'{source}: agent.{key}: must be positive')
    for image in docker.get('images', []):
        if not image or re.search(r'[\s#]', image):
            raise ValueError(f'{source}: docker.images: {image!r} is not an image name')
    for item in proxy.get('connectors', []):
        if not CONNECTOR.fullmatch(item):
            raise ValueError(f'{source}: proxy.connectors: {item!r} is not a connector ID (mcpsrv_...)')
    check_hosts(proxy.get('hosts', {}), f'{source}: proxy.hosts')
    for key, value in subnets.items():
        match = SUBNET.fullmatch(value)
        if not match or any(int(octet) > 255 for octet in match.groups()):
            raise ValueError(f'{source}: subnets.{key}: expected the first three octets of an IPv4 /24, as "10.99.0"')


def merge(base, override, path=()):
    """base with override's values; tables merge, except each host of proxy.hosts."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict) and path != ('proxy', 'hosts'):
            merged[key] = merge(merged[key], value, path + (key,))
        else:
            merged[key] = value
    return merged


def read(path):
    try:
        text = path.read_text()
    except FileNotFoundError:
        return {}
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f'{path.name}: {error}') from None


def resolve(value, base):
    """A path setting: ~ expanded, relative to base, absolute."""
    return Path(os.path.abspath(base / Path(value).expanduser()))


def load(root=ROOT):
    """The checked settings of root's workspace, with the defaults and paths resolved."""
    directory = Path(root) / '.devcontainer'
    settings = copy.deepcopy(DEFAULTS)
    for name in FILES:
        data = read(directory / name)
        check(data, name)
        settings = merge(settings, data)
    subnets = settings['subnets']
    if subnets['proxy'] and subnets['proxy'] == subnets['dind']:
        raise ValueError('subnets: proxy and dind must differ')
    if settings['docker']['enabled'] and settings['agent']['uid'] in SUBORDINATE_IDS:
        raise ValueError(f'agent.uid: {settings["agent"]["uid"]} is used by DinD; with docker.enabled it must be '
                         f'outside {SUBORDINATE_IDS.start}-{SUBORDINATE_IDS.stop - 1}')
    settings['sumi']['secrets'] = resolve(settings['sumi']['secrets'], directory)
    settings['proxy']['ca'] = settings['proxy']['ca'] and resolve(settings['proxy']['ca'], directory)
    # ~ may bring them in, and settings.env cannot hold them.
    for name, path in (('sumi.secrets', settings['sumi']['secrets']), ('proxy.ca', settings['proxy']['ca'])):
        if "'" in str(path) or '\n' in str(path):
            raise ValueError(f"{name}: {path}: newlines and ' are not allowed")
    return settings


def outside_settings(root=ROOT, environ=os.environ):
    """The names of settings.env's variables that the environment or .devcontainer/.env sets."""
    found = [f'{name} in the environment' for name in VARIABLES if name in environ]
    try:
        lines = (Path(root) / '.devcontainer/.env').read_text().splitlines()
    except FileNotFoundError:
        lines = []
    # compose's dotenv takes KEY=value, KEY: value and export KEY=value.
    keys = {match.group(1) for match in map(DOTENV_KEY.match, lines) if match}
    found += [f'{name} in .devcontainer/.env' for name in VARIABLES if name in keys]
    return found


def variables(settings):
    """{name: value} for settings.env."""
    secrets = settings['sumi']['secrets']
    values = {
        PREFIX + 'UID': str(settings['agent']['uid']),
        PREFIX + 'GID': str(settings['agent']['gid']),
        PREFIX + 'DIND': '1' if settings['docker']['enabled'] else '0',
        PREFIX + 'NIX': '1' if settings['nix']['enabled'] else '0',
        PREFIX + 'NIX_REF': settings['nix']['ref'],
        PREFIX + 'NIX_SHELL': settings['nix']['shell'],
        PREFIX + 'SUMI': '1' if settings['sumi']['enabled'] else '0',
        PREFIX + 'SUMI_DIR': str(secrets.parent),
        PREFIX + 'SUMI_FILE': secrets.name,
        PREFIX + 'PROXY_CA': str(settings['proxy']['ca'] or ''),
        PREFIX + 'CONNECTORS': ','.join(settings['proxy']['connectors']),
    }
    # Unset, pick-networks.py chooses them in networks.env.
    for key in ('proxy', 'dind'):
        if settings['subnets'][key]:
            values[f'{PREFIX}{key.upper()}_NET'] = settings['subnets'][key]
    for name, value in values.items():
        # Single quotes keep compose from interpolating; nothing can escape them.
        if "'" in value or '\n' in value:
            raise ValueError(f"{name}: {value!r}: newlines and ' are not allowed")
    return values


def proxy_json(settings):
    hosts = {host: without_env(entry) for host, entry in settings['proxy']['hosts'].items()}
    return json.dumps({'hosts': hosts}, indent=2) + '\n'


def images_txt(settings):
    return ''.join(image + '\n' for image in settings['docker']['images'])


def dummies(settings):
    return sorted({name for entry in settings['proxy']['hosts'].values() for name in entry.get('env', [])})


def write(path, text):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(text)
    temporary.replace(path)


def node_modules_paths(root=ROOT, environ=os.environ):
    root = Path(root).resolve()
    state = Path(environ.get('XDG_STATE_HOME') or Path.home() / '.local/state').resolve()
    state = state / 'claude-devcontainer/node-modules' / hashlib.sha256(os.fsencode(root)).hexdigest()[:24]
    if state.is_relative_to(root):
        raise ValueError('Node overlay state must be outside the repository and its build contexts.')
    paths = {'LOWER': (root / 'node_modules').resolve(), 'UPPER': state / 'upper', 'WORK': state / 'work'}
    for path in paths.values():
        if re.search(r"[\s,:\\']", str(path)):
            raise ValueError(f'Node overlay path cannot contain whitespace, commas, colons, backslashes or quotes: {path}')
    return {PREFIX + 'NODE_' + key: str(path) for key, path in paths.items()}


def prepare_node_modules(root=ROOT, environ=os.environ):
    # Never clear upper/work here: changes survive ordinary recreation.
    for path in node_modules_paths(root, environ).values():
        Path(path).mkdir(parents=True, exist_ok=True)


def generate(root=ROOT, environ=os.environ):
    """Check the settings and the files they name, and write .generated/."""
    outside = outside_settings(root, environ)
    if outside:
        raise ValueError(
            'the settings come only from .devcontainer/claude.toml and claude.local.toml, but found '
            + ', '.join(outside) + '. Move them there. Inside the agent, compose sets some of them; '
            'run with them removed, as `env -u CLAUDE_DEVCONTAINER_DIND -u CLAUDE_DEVCONTAINER_NIX '
            '-u CLAUDE_DEVCONTAINER_SUMI devcontainer up`.')
    settings = load(root)
    secrets = settings['sumi']['secrets']
    if settings['sumi']['enabled'] and not secrets.is_file():
        raise ValueError(f'sumi.enabled needs {secrets} (sumi.secrets), one secret per line, with mode 0600.')
    ca = settings['proxy']['ca']
    if ca and not ca.is_file():
        raise ValueError(f'proxy.ca names {ca}, which is not a file.')
    out = Path(root) / '.devcontainer/.generated'
    out.mkdir(parents=True, exist_ok=True)
    (out / '.gitignore').write_text('*\n')
    header = '# Written by scripts/settings.py from claude.toml and claude.local.toml; do not edit.\n'
    values = variables(settings) | node_modules_paths(root, environ)
    write(out / 'settings.env', header + ''.join(f"{name}='{value}'\n" for name, value in values.items()))
    write(out / 'proxy.json', proxy_json(settings))
    write(out / 'images.txt', images_txt(settings))
    write(out / 'agent.env', header + ''.join(f'{name}={DUMMY}\n' for name in dummies(settings)))
    return settings


def main():
    try:
        if sys.argv[1:] == ['generate']:
            generate()
        elif sys.argv[1:] == ['prepare-dependencies']:
            prepare_node_modules()
        elif len(sys.argv) == 3 and sys.argv[1] == 'proxy-json':
            path = Path(sys.argv[2])
            data = read(path)
            check(data, path.name)
            print(proxy_json(merge(copy.deepcopy(DEFAULTS), data)), end='')
        else:
            sys.exit('usage: settings.py generate | prepare-dependencies | proxy-json FILE')
    except ValueError as error:
        sys.exit(f'error: {error}')


if __name__ == '__main__':
    main()
