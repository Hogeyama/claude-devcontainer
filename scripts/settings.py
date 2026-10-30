#!/usr/bin/env python3
"""Global claude.toml, workspace claude.toml, and claude.local.toml settings.

CONFIG/claude.toml supplies user defaults before the two workspace files.

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
  agent.env     agent.env, and the dummy tokens that proxy.hosts' env names, for the agent.
  global-agent-setup.sh  a copy of CONFIG/agent-setup.sh, which the agent image
                build runs before the workspace's agent-setup.sh.
  workspace.compose.yaml  workspace mounts, the optional Nix store mount,
                agent.mounts, the host's session history (agent.share_projects) and DinD's
                data over the shared base (docker.shared_base).
  hidden.txt    the masked paths of the shared working tree, for the agent's Git.

The settings come from these files only. A variable of settings.env in the
environment or in .devcontainer/.env would win over settings.env in compose
alone, so `generate` refuses to run with one.

Usage, from anywhere:
  settings.py generate         check the settings and write .generated/
  settings.py prepare-dependencies  create persistent DinD and :cow overlay directories
  settings.py check-shared-mounts   validate the host working tree's mounts before startup
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
import shlex
import subprocess
import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parent))
import layout  # noqa: E402

# The workspace root (scripts/layout.py).
ROOT = layout.workspace_root()
FILES = ('claude.toml', 'claude.local.toml')
CONFIG = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'claude-devcontainer'
# {table: {key: default}}; the defaults also give each setting's type.
DEFAULTS = {
    'git': {'isolation': 'clone', 'shared_hidden_paths': [], 'inherit_remotes': True},
    'agent': {'uid': 1000, 'gid': 1000, 'extra_path': [], 'env': {}, 'share_projects': False, 'mounts': []},
    'acp': {'enabled': False, 'version': '0.90.0'},
    'docker': {'enabled': False, 'images': [], 'shared_base': ''},
    'nix': {'enabled': False, 'ref': 'HEAD', 'shell': ''},
    'sumi': {'enabled': False, 'secrets': str(CONFIG / 'sumi/secrets.txt')},
    'codeServer': {'enabled': False, 'extensions': []},
    'proxy': {'ca': '', 'connectors': [], 'web_search': False, 'hosts': {}},
    'subnets': {'proxy': '', 'dind': ''},
}
HOST_FIELDS = {'routes', 'headers', 'inject', 'http', 'env'}
ISOLATION_MODES = ('clone', 'shared', 'unsafe_shared')
# The modes that mount the host's working tree as /workspace.
SHARED_MODES = ('shared', 'unsafe_shared')
# Where shared and clone modes show the agent the host's Git, read-only.
HOST_GIT = '/run/host/git'
# compose's names, which settings.env writes. Only these may not come from
# elsewhere; compose sets others, such as _PROXY_ADDRESS, in the containers.
PREFIX = 'CLAUDE_DEVCONTAINER_'
VARIABLES = tuple(PREFIX + name for name in (
    'GIT_ISOLATION', 'UID', 'GID', 'PROXY_NET', 'DIND_NET', 'DIND', 'DIND_SHARED', 'NIX', 'NIX_REF', 'NIX_SHELL', 'SUMI', 'SUMI_DIR', 'SUMI_FILE',
    'CODE_SERVER', 'CODE_SERVER_EXTENSIONS', 'ACP', 'ACP_VERSION', 'PROXY_CA', 'CONNECTORS', 'WEB_SEARCH'))
DUMMY = 'managed-by-proxy'
DOTENV_KEY = re.compile(r'\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_.-]*)\s*(?:[=:]|$)')
ENV_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
# What compose sets in the agent; a dummy may not replace it.
RESERVED_ENV = {'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'DOCKER_HOST', 'CLAUDE_CODE_OAUTH_TOKEN'}
RESERVED_ENV_PREFIXES = ('TESTCONTAINERS_', PREFIX)
CONNECTOR = re.compile(r'mcp(srv|rs)_[A-Za-z0-9]+')
# A Marketplace extension ID, publisher.name, optionally @version.
EXTENSION = re.compile(r'[A-Za-z0-9][A-Za-z0-9-]*\.[A-Za-z0-9][A-Za-z0-9-]*(@[0-9][0-9A-Za-z.+-]*)?')
# Exact package versions only, including prereleases; no moving tags or ranges.
ACP_VERSION = re.compile(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)'
                         r'(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?'
                         r'(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?')
# A shared base's name, which also names a Compose volume.
BASE_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*')
# DinD's data directory.
DIND_DATA = '/home/rootless/.local/share/docker'
AGENT_HOME = '/home/node'
# Where agent.mounts may not go: what this environment mounts or manages
# itself, which a mount would hide or replace.
# Under /workspace, only :cow mounts of dependencies go, beside these.
MANAGED_WORKSPACE = ('/workspace/.git', '/workspace/.devcontainer')
MANAGED_TARGETS = ('/run', '/var/run', '/public-ca', '/nix', '/home/node/.claude',
                   '/home/node/.vscode-server', '/etc/claude-code', '/usr/local/bin', '/proc', '/sys', '/dev')
# Claude Code's session history of /workspace, the agent's working directory.
AGENT_PROJECT = '/home/node/.claude/projects/-workspace'
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
    if data.get('git', {}).get('isolation', 'clone') not in ISOLATION_MODES:
        raise ValueError(f'{source}: git.isolation: expected "clone" or "shared" or "unsafe_shared"')
    for value in data.get('git', {}).get('shared_hidden_paths', []):
        path = Path(value)
        if not value or path.is_absolute() or path == Path('.') or '..' in path.parts or '\n' in value or '\r' in value:
            raise ValueError(f'{source}: git.shared_hidden_paths: expected a path below the repository root: {value!r}')
    shell_path(data.get('agent', {}).get('extra_path', []), str(source))
    for value in data.get('agent', {}).get('mounts', []):
        parse_mount(value, f'{source}: agent.mounts')
    for name, value in data.get('agent', {}).get('env', {}).items():
        if not ENV_NAME.fullmatch(name):
            raise ValueError(f'{source}: agent.env: {name!r} is not an environment variable name')
        if name.upper() in RESERVED_ENV or name.upper().startswith(RESERVED_ENV_PREFIXES):
            raise ValueError(f'{source}: agent.env: {name} is set by compose')
        # Keep each assignment on one line in agent.env.
        if not isinstance(value, str) or '\n' in value or "'" in value:
            raise ValueError(f"{source}: agent.env.{name}: expected a string without newlines or '")
    if 'version' in data.get('acp', {}) and not ACP_VERSION.fullmatch(data['acp']['version']):
        raise ValueError(f'{source}: acp.version: expected an exact version, such as "0.90.0"')
    agent, docker, proxy, subnets = (data.get(table, {}) for table in ('agent', 'docker', 'proxy', 'subnets'))
    for key in ('uid', 'gid'):
        if agent.get(key, 1) <= 0:
            raise ValueError(f'{source}: agent.{key}: must be positive')
    for image in docker.get('images', []):
        if not image or re.search(r'[\s#]', image):
            raise ValueError(f'{source}: docker.images: {image!r} is not an image name')
    if docker.get('shared_base') and not BASE_NAME.fullmatch(docker['shared_base']):
        raise ValueError(f'{source}: docker.shared_base: {docker["shared_base"]!r} is not a name '
                         '(letters, digits, _ . -)')
    for item in data.get('codeServer', {}).get('extensions', []):
        if not EXTENSION.fullmatch(item):
            raise ValueError(f'{source}: codeServer.extensions: {item!r} is not an extension ID (publisher.name)')
    for item in proxy.get('connectors', []):
        if not CONNECTOR.fullmatch(item):
            raise ValueError(f'{source}: proxy.connectors: {item!r} is not a connector ID (mcpsrv_...)')
    check_hosts(proxy.get('hosts', {}), f'{source}: proxy.hosts')
    for key, value in subnets.items():
        match = SUBNET.fullmatch(value)
        if not match or any(int(octet) > 255 for octet in match.groups()):
            raise ValueError(f'{source}: subnets.{key}: expected the first three octets of an IPv4 /24, as "10.99.0"')


MOUNT_MODES = ('ro', 'rw', 'cow')


def parse_mount(value, where='agent.mounts'):
    """SOURCE:TARGET[:ro|rw|cow] as (source, target, mode); ro unless given.

    A target of ~/... is under the agent's home, /home/node. A target under
    /workspace takes only :cow, for dependencies such as node_modules; written
    as /workspace/PATH:cow alone, its source is the repository's own PATH
    (source '' here, resolved by cow_source).
    """
    parts = value.split(':')
    if len(parts) == 2 and parts[1] == 'cow' and parts[0].startswith('/workspace/'):
        parts = ['', *parts]
    if len(parts) not in (2, 3) or (len(parts) == 3 and parts[2] not in MOUNT_MODES):
        raise ValueError(f'{where}: {value!r}: expected SOURCE:TARGET, with :ro, :rw or :cow after it or not, '
                         'or /workspace/PATH:cow')
    source, target = parts[:2]
    if (not source and len(parts) != 3) or ',' in value or re.search(r'[\x00-\x1f]', value) or (
            not source and parts[2] != 'cow'):
        raise ValueError(f'{where}: {value!r}: expected a source path without commas or control characters')
    if target.startswith('~/'):
        target = AGENT_HOME + target[1:]
    path = Path(target)
    # The home itself would hide the image's files there.
    if (not path.is_absolute() or str(path) != target.rstrip('/') or '..' in path.parts
            or path in (Path('/'), Path(AGENT_HOME))
            or any(path == Path(managed) or path.is_relative_to(managed) for managed in MANAGED_TARGETS)):
        raise ValueError(f'{where}: {value!r}: the target must be an absolute, normalized path outside '
                         + ', '.join(MANAGED_TARGETS))
    mode = parts[2] if len(parts) == 3 else 'ro'
    if path == Path('/workspace') or path.is_relative_to('/workspace') and (
            mode != 'cow' or any(path == Path(managed) or path.is_relative_to(managed) for managed in MANAGED_WORKSPACE)):
        raise ValueError(f'{where}: {value!r}: under /workspace, only :cow mounts go, and not at '
                         + ', '.join(['/workspace', *MANAGED_WORKSPACE]))
    return source, str(path), mode


def resolve_mount(value, base):
    """A mount with its source resolved like the other paths: ~ expanded, relative to base."""
    source, target, mode = parse_mount(value)
    if not source:
        return f'{target}:{mode}'
    return f'{resolve(source, base)}:{target}:{mode}'


def cow_source(source, target, root):
    """A :cow mount's source: the repository's own path for /workspace/PATH:cow."""
    if source:
        return source
    return str(Path(root).resolve() / Path(target).relative_to('/workspace'))


def cow_paths(source, target, root=ROOT, environ=os.environ):
    """The volume and this workspace's upper and work directories of a :cow mount.

    Named after the source and target too, as a volume's options are fixed
    when Docker creates it.
    """
    root = Path(root).resolve()
    state = Path(environ.get('XDG_STATE_HOME') or Path.home() / '.local/state').resolve() / 'claude-devcontainer'
    key = hashlib.sha256(os.fsencode(f'{source}\0{target}')).hexdigest()[:16]
    own = state / 'cow' / hashlib.sha256(os.fsencode(root)).hexdigest()[:24] / key
    if own.is_relative_to(root):
        raise ValueError('agent.mounts: the :cow state must be outside the repository and its build contexts.')
    for path in (Path(source), own):
        if re.search(r"[\s,:\\'=]", str(path)):
            raise ValueError(f'agent.mounts: a :cow path cannot contain whitespace, commas, colons, equals signs, '
                             f'backslashes or quotes: {path}')
    return {'volume': 'cow-' + key, 'upper': own / 'upper', 'work': own / 'work'}


def prepare_cow_mounts(settings, root=ROOT, environ=os.environ):
    """Create the upper and work directories of the :cow mounts."""
    # Never clear them: the agent's changes survive ordinary recreation.
    for value in settings['agent']['mounts']:
        source, target, mode = parse_mount(value)
        if mode == 'cow':
            paths = cow_paths(cow_source(source, target, root), target, root, environ)
            for key in ('upper', 'work'):
                paths[key].mkdir(parents=True, exist_ok=True)


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
    directory = layout.config_dir(root)
    settings = copy.deepcopy(DEFAULTS)
    # Host mode reads no file of the repository, which may be anyone's.
    workspace = () if layout.host_mode() else tuple(directory / name for name in FILES)
    for source in (CONFIG / 'claude.toml', *workspace):
        data = read(source)
        check(data, str(source))
        # Resolve relative file paths against the file that supplied them.
        for table, key in (('sumi', 'secrets'), ('proxy', 'ca')):
            value = data.get(table, {}).get(key)
            if value:
                data[table][key] = str(resolve(value, source.parent))
        if 'mounts' in data.get('agent', {}):
            data['agent']['mounts'] = [resolve_mount(value, source.parent) for value in data['agent']['mounts']]
        settings = merge(settings, data)
    subnets = settings['subnets']
    if subnets['proxy'] and subnets['proxy'] == subnets['dind']:
        raise ValueError('subnets: proxy and dind must differ')
    if settings['docker']['enabled'] and settings['agent']['uid'] in SUBORDINATE_IDS:
        raise ValueError(f'agent.uid: {settings["agent"]["uid"]} is used by DinD; with docker.enabled it must be '
                         f'outside {SUBORDINATE_IDS.start}-{SUBORDINATE_IDS.stop - 1}')
    both = sorted(set(settings['agent']['env']) & set(dummies(settings)))
    if both:
        raise ValueError(f'agent.env: {", ".join(both)} is also the env of a host in proxy.hosts, '
                         'which the proxy replaces')
    if settings['codeServer']['extensions'] and not settings['codeServer']['enabled']:
        raise ValueError('codeServer.extensions: needs codeServer.enabled')
    # The ACP bridge maps the agent's /workspace paths to the host's files and
    # back, which only holds while the agent edits the host's working tree.
    if settings['acp']['enabled'] and settings['git']['isolation'] not in SHARED_MODES:
        raise ValueError('acp.enabled: needs git.isolation = "shared" or "unsafe_shared"')
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
        lines = (layout.config_dir(root) / '.env').read_text().splitlines()
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
        PREFIX + 'GIT_ISOLATION': settings['git']['isolation'],
        PREFIX + 'UID': str(settings['agent']['uid']),
        PREFIX + 'GID': str(settings['agent']['gid']),
        PREFIX + 'DIND': '1' if settings['docker']['enabled'] else '0',
        PREFIX + 'DIND_SHARED': shared_base(settings),
        PREFIX + 'NIX': '1' if settings['nix']['enabled'] else '0',
        PREFIX + 'NIX_REF': settings['nix']['ref'],
        PREFIX + 'NIX_SHELL': settings['nix']['shell'],
        PREFIX + 'SUMI': '1' if settings['sumi']['enabled'] else '0',
        PREFIX + 'SUMI_DIR': str(secrets.parent),
        PREFIX + 'SUMI_FILE': secrets.name,
        PREFIX + 'CODE_SERVER_EXTENSIONS': ' '.join(settings['codeServer']['extensions']),
        PREFIX + 'CODE_SERVER': '1' if settings['codeServer']['enabled'] else '0',
        PREFIX + 'ACP': '1' if settings['acp']['enabled'] else '0',
        PREFIX + 'ACP_VERSION': settings['acp']['version'],
        PREFIX + 'PROXY_CA': str(settings['proxy']['ca'] or ''),
        PREFIX + 'CONNECTORS': ','.join(settings['proxy']['connectors']),
        PREFIX + 'WEB_SEARCH': '1' if settings['proxy']['web_search'] else '0',
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


def agent_env(settings, header=''):
    """agent.env for compose's env_file: agent.env, then proxy.hosts' dummies."""
    # Double quotes let us escape a trailing backslash without swallowing the
    # closing quote. Escape dollars too, so Compose cannot interpolate values.
    lines = []
    for name, value in settings['agent']['env'].items():
        value = value.replace('\\', '\\\\').replace('"', '\\"').replace('$', '$$').replace('\r', '\\r')
        lines.append(f'{name}="{value}"\n')
    return header + ''.join(lines) + ''.join(f'{name}={DUMMY}\n' for name in dummies(settings))


def write(path, text):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(text)
    temporary.replace(path)


def shared_base(settings):
    """docker.shared_base, or '' without DinD, so that the global claude.toml can name it."""
    return settings['docker']['shared_base'] if settings['docker']['enabled'] else ''


def dind_paths(name, root=ROOT, environ=os.environ):
    """The shared base of DinD's data and this workspace's overlay over it.

    base/data is the lower: the data of a dockerd that dind-base.py runs, and
    base/uid the agent.uid that owns it. upper and work are this workspace's.
    """
    root = Path(root).resolve()
    state = Path(environ.get('XDG_STATE_HOME') or Path.home() / '.local/state').resolve() / 'claude-devcontainer'
    base = state / 'dind-base' / name
    own = state / 'dind' / hashlib.sha256(os.fsencode(root)).hexdigest()[:24] / name
    paths = {'base': base, 'data': base / 'data', 'uid': base / 'uid', 'upper': own / 'upper', 'work': own / 'work'}
    for path in paths.values():
        if re.search(r"[\s,:\\'=]", str(path)):
            raise ValueError(f'DinD overlay path cannot contain whitespace, commas, colons, equals signs, '
                             f'backslashes or quotes: {path}')
    return paths


def prepare_dind(settings, root=ROOT, environ=os.environ):
    """Create the shared base and this workspace's overlay directories; check the base's UID."""
    name = shared_base(settings)
    if not name:
        return
    paths = dind_paths(name, root, environ)
    uid = str(settings['agent']['uid'])
    if paths['uid'].is_file() and paths['uid'].read_text().strip() != uid:
        raise ValueError(f'docker.shared_base: {name} belongs to agent.uid = {paths["uid"].read_text().strip()}, '
                         f'not {uid}; DinD could not read its files. Use another base.')
    # Never clear upper/work: DinD's data survives ordinary recreation.
    for key in ('data', 'upper', 'work'):
        paths[key].mkdir(parents=True, exist_ok=True)
    paths['uid'].write_text(uid + '\n')


def check_shared_repository(root, mode='shared'):
    """Shared Git must be self-contained at the same relative container paths."""
    root = Path(root).resolve()
    git = root / '.git'
    advice = f'git.isolation = "{mode}" needs a repository with an internal .git directory; use "clone" for linked worktrees or external Git storage.'
    if git.is_symlink() or not git.is_dir():
        raise ValueError(advice)
    result = subprocess.run(['git', '-C', str(root), 'rev-parse', '--path-format=absolute',
                             '--show-toplevel', '--git-dir', '--git-common-dir', '--git-path', 'objects'],
                            capture_output=True, text=True)
    if result.returncode or result.stdout.splitlines() != [str(root), str(git), str(git), str(git / 'objects')]:
        raise ValueError(advice)
    worktree = subprocess.run(['git', '-C', str(root), 'config', '--path', '--get', 'core.worktree'],
                             capture_output=True, text=True)
    if worktree.returncode not in (0, 1) or (worktree.returncode == 0 and Path(worktree.stdout.strip()).is_absolute()):
        raise ValueError(f'{advice} Absolute core.worktree paths cannot be used inside /workspace.')
    if mode == 'unsafe_shared':
        for config in ('config', 'config.worktree'):
            if not (git / config).exists():
                continue
            custom = subprocess.run(
                ['git', 'config', '--file', str(git / config), '--no-includes', '--get-regexp',
                 r'^(core\.hookspath|include\.path|includeif\..*\.path)$'],
                capture_output=True, text=True)
            if custom.returncode != 1:
                raise ValueError('unsafe_shared does not support core.hooksPath or included Git configuration; '
                                 'use clone or shared.')
    # Alternates and symlinks may refer to paths absent from /workspace.
    for directory, dirs, files in os.walk(git):
        for name in dirs + files:
            path = Path(directory) / name
            if path.is_symlink() or (name in ('alternates', 'http-alternates') and path.stat().st_size):
                raise ValueError(f'{advice} Unsupported Git storage: {path}')


def check_mount_path(root, relative):
    """Do not resolve away symlinks in a mount destination, including its leaf."""
    path = Path(root)
    for part in relative.parts:
        path /= part
        if path.is_symlink():
            raise ValueError(f'shared workspace mount must not follow a symlink: {path}')
        if path != Path(root) / relative and not path.is_dir():
            raise ValueError(f'shared workspace mount needs an existing, non-symlink parent: {path}')
    return path


def shared_mounts(settings, root):
    """Mandatory protections and secret masks; reserve unsafe_shared protection paths."""
    root = Path(root).resolve()
    # Host mode keeps its state outside the repository, so there is no
    # .devcontainer to protect.
    managed = () if layout.host_mode() else ('.devcontainer',)
    for name in ('.git', *managed):
        if not check_mount_path(root, Path(name)).is_dir():
            raise ValueError(f'shared workspace mount needs a directory: {root / name}')

    def read_only(name, target):
        return {'type': 'bind', 'source': str(root / name), 'target': target,
                'read_only': True, 'bind': {'create_host_path': False}}
    mounts = [read_only(name, '/workspace/' + name) for name in managed]
    if settings['git']['isolation'] == 'unsafe_shared':
        # Pin ancestors too: otherwise renaming .git or objects could bypass
        # the protected children by creating a replacement directory.
        for name in ('.git', '.git/objects'):
            mounts.append({'type': 'bind', 'source': str(root / name),
                           'target': '/workspace/' + name, 'bind': {'create_host_path': False}})
        # Pin configuration files against replacement and mask executable hooks
        # and auxiliary configuration (attributes, alternates).
        for name in ('hooks', 'info', 'objects/info'):
            path = check_mount_path(root, Path('.git') / name)
            path.mkdir(exist_ok=True)
            mounts.append({'type': 'tmpfs', 'target': '/workspace/.git/' + name,
                           'read_only': True, 'tmpfs': {'size': 4096, 'mode': 0o555}})
        for name in ('config', 'config.worktree'):
            path = check_mount_path(root, Path('.git') / name)
            # Reserve absent files too, so they cannot be introduced by the agent.
            if not path.exists():
                with path.open('x'):
                    pass
            if not path.is_file():
                raise ValueError(f'Expected a Git configuration file: {path}')
            mounts.append(read_only('.git/' + name, '/workspace/.git/' + name))
        # Submodules' Git directories, with their own config and hooks that the
        # host's Git runs in them: read-only, reserved when absent so that the
        # agent cannot add one. Submodules are then read-only to the agent.
        path = check_mount_path(root, Path('.git/modules'))
        path.mkdir(exist_ok=True)
        mounts.append(read_only('.git/modules', '/workspace/.git/modules'))
    else:
        mounts.append({'type': 'volume', 'source': 'agent-git', 'target': '/workspace/.git',
                       'volume': {'nocopy': True}})
        mounts.append(read_only('.git', HOST_GIT))
    hidden = hidden_paths(settings, root)
    pinned = {Path(mount['target']) for mount in mounts}
    read_only_roots = [Path(mount['target']) for mount in mounts if mount.get('read_only')]
    for relative in hidden:
        # A mask moves with its parent. Pin every ancestor so that the agent
        # cannot rename it and leave a decoy to be masked on the next start.
        # Do not reopen a subtree already protected by a read-only mount.
        for parent in reversed(relative.parents):
            target = Path('/workspace') / parent
            if (parent == Path('.') or target in pinned
                    or any(target.is_relative_to(path) for path in read_only_roots)):
                continue
            mounts.append({'type': 'bind', 'source': str(root / parent),
                           'target': str(target), 'bind': {'create_host_path': False}})
            pinned.add(target)
    for relative in hidden:
        path = root / relative
        target = '/workspace/' + relative.as_posix()
        if path.is_dir():
            mounts.append({'type': 'tmpfs', 'target': target, 'read_only': True,
                           'tmpfs': {'size': 4096, 'mode': 0o555}})
        else:
            mounts.append({'type': 'bind', 'source': str(layout.config_dir(root) / '.generated/empty-secret'),
                           'target': target, 'read_only': True, 'bind': {'create_host_path': False}})
    return mounts


def hidden_paths(settings, root):
    """The existing files and directories to mask, relative to the root, parents first."""
    root = Path(root).resolve()
    candidates = {}
    secrets = settings['sumi']['secrets']
    if not secrets.is_relative_to(root):
        # A host-side alias can still name a file in the shared working tree.
        secrets = secrets.resolve()
    if secrets.is_relative_to(root):
        candidates[secrets.relative_to(root)] = False
    candidates.update({Path(value): True for value in settings['git']['shared_hidden_paths']})
    hidden = []
    for relative, required in sorted(candidates.items(), key=lambda item: (len(item[0].parts), str(item[0]))):
        # Management inputs also have /run/host aliases. Do not promise to hide
        # data there while leaving the alias readable, or mask Git itself.
        for reserved in map(Path, ('.git', '.devcontainer/.generated', '.devcontainer/.nix-env')):
            if relative.is_relative_to(reserved) or reserved.is_relative_to(relative):
                raise ValueError(f'Cannot hide shared management path: {relative}; move secrets outside it.')
        # An absent, disabled sumi file needs no mount. Still inspect existing
        # components so dangling links are rejected.
        path = root
        for part in relative.parts:
            path /= part
            if path.is_symlink():
                raise ValueError(f'shared workspace mount must not follow a symlink: {path}')
            if not path.exists():
                break
        path = root / relative
        if not path.exists():
            if required:
                raise ValueError(f'git.shared_hidden_paths: path does not exist: {relative}')
            continue
        check_mount_path(root, relative)
        if any(relative.is_relative_to(parent) for parent in hidden):
            continue
        if not path.is_dir() and not path.is_file():
            raise ValueError(f'Hidden shared path must be a regular file or directory: {relative}')
        hidden.append(relative)
    return hidden


def host_git_dir(root):
    """The host's common Git directory for clone mode to show, or None.

    The common directory, so that a linked worktree shows every branch. Not
    when objects come from alternates, which would be missing there.
    """
    result = subprocess.run(['git', '-C', str(root), 'rev-parse', '--path-format=absolute', '--git-common-dir'],
                            capture_output=True, text=True)
    if result.returncode:
        return None
    git = Path(result.stdout.strip())
    alternates = git / 'objects/info/alternates'
    if not git.is_dir() or (alternates.is_file() and alternates.stat().st_size):
        return None
    return git


def host_project(root, environ=os.environ):
    """The host's Claude Code session history of root, named as Claude Code names it."""
    root = Path(root).resolve()
    name = re.sub(r'[^A-Za-z0-9]', '-', str(root))
    # Claude Code shortens longer names with a hash of its own.
    if len(name) > 200:
        raise ValueError(f'agent.share_projects: {root} is too long a path to find its Claude Code history.')
    config = Path(environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude')
    return config / 'projects' / name


def workspace_compose(settings, root, environ=os.environ):
    mounts = [{'type': 'volume', 'source': 'workspace', 'target': '/workspace'}]
    if settings['git']['isolation'] in SHARED_MODES:
        check_shared_repository(root, settings['git']['isolation'])
        mounts = [{'type': 'bind', 'source': str(Path(root).resolve()),
                   'target': '/workspace', 'bind': {'create_host_path': False}}]
        mounts += shared_mounts(settings, root)
    elif git := host_git_dir(root):
        mounts.append({'type': 'bind', 'source': str(git), 'target': HOST_GIT,
                       'read_only': True, 'bind': {'create_host_path': False}})
    if settings['nix']['enabled']:
        mounts.append({'type': 'bind', 'source': '/nix/store', 'target': '/nix/store',
                       'read_only': True, 'bind': {'create_host_path': False}})
    volumes = {}
    for value in settings['agent']['mounts']:
        source, target, mode = parse_mount(value)
        if not source:
            source = cow_source(source, target, root)
            # Only where the repository has it, so that the global claude.toml can name it.
            if not Path(source).is_dir():
                continue
        if not Path(source).exists():
            raise ValueError(f'agent.mounts: {source} does not exist on the host.')
        if mode != 'cow':
            mounts.append({'type': 'bind', 'source': source, 'target': target, 'read_only': mode == 'ro',
                           'bind': {'create_host_path': False}})
            continue
        if not Path(source).is_dir():
            raise ValueError(f'agent.mounts: {source}: a :cow source must be a directory.')
        # The host's directory as the lower, like the Node overlay: the agent
        # writes to an upper directory of this workspace, never to the source.
        paths = cow_paths(source, target, root, environ)
        mounts.append({'type': 'volume', 'source': paths['volume'], 'target': target, 'volume': {'nocopy': True}})
        volumes[paths['volume']] = {'driver_opts': {
            'type': 'overlay', 'device': 'overlay',
            'o': f'lowerdir={source},upperdir={paths["upper"]},workdir={paths["work"]}'}}
    if settings['agent']['share_projects']:
        # The host's history of this repository as the agent's of /workspace,
        # so that either can resume the other's sessions.
        project = host_project(root, environ)
        project.mkdir(parents=True, exist_ok=True)
        mounts.append({'type': 'bind', 'source': str(project), 'target': AGENT_PROJECT,
                       'bind': {'create_host_path': False}})
    compose = {'services': {'agent': {'volumes': mounts}}}
    if name := shared_base(settings):
        # Copy-on-write over the shared base, like the Node overlay; DinD
        # stores its data with fuse-overlayfs then, since the kernel's overlay
        # cannot keep its upper directories on this overlay. A volume of its
        # own name, as the options of one are fixed when Docker creates it.
        paths = dind_paths(name, root, environ)
        volume = 'dind-shared-' + name
        compose['services']['dind'] = {'volumes': [{'type': 'volume', 'source': volume, 'target': DIND_DATA}]}
        volumes[volume] = {'driver_opts': {
            'type': 'overlay', 'device': 'overlay',
            'o': f'lowerdir={paths["data"]},upperdir={paths["upper"]},workdir={paths["work"]}'}}
    if volumes:
        compose['volumes'] = volumes
    # JSON is YAML; escape Compose interpolation separately from serialization.
    return json.dumps(compose, indent=2).replace('$', '$$') + '\n'


def check_shared_mounts(root=ROOT):
    """Check all destinations and mandatory protections before container startup."""
    settings = load(root)
    mode = settings['git']['isolation']
    if mode not in SHARED_MODES:
        return
    result = subprocess.run(['docker', 'compose', '-f', str(layout.compose_file(root)),
                             'config', '--format', 'json'], check=True, capture_output=True, text=True)
    config = json.loads(result.stdout)
    agent = config['services']['agent']
    mounts = agent.get('volumes', []) + [
        {'type': 'tmpfs', 'target': item.split(':', 1)[0]}
        for item in agent.get('tmpfs', [])]
    workspace = [mount for mount in mounts if mount['target'] == '/workspace']
    if (len(workspace) != 1 or workspace[0]['type'] != 'bind'
            or Path(workspace[0]['source']).resolve() != Path(root).resolve()):
        raise ValueError(f'git.isolation = "{mode}" needs .generated/workspace.compose.yaml in '
                         'compose.yaml include.path; update it from claude/template/compose.yaml.')
    for mount in mounts:
        target = Path(mount['target'])
        if not target.is_relative_to('/workspace') or target == Path('/workspace'):
            continue
        relative = target.relative_to('/workspace')
        if '..' in relative.parts:
            raise ValueError(f'Invalid shared mount target: {target}')
        check_mount_path(root, relative)
    # Consumer overrides must not silently disable a protection or expose a
    # child of a masked directory through another mount.
    protections = shared_mounts(settings, root)
    protected_targets = {m['target'] for m in protections}
    for expected in protections:
        actual = [m for m in mounts if m['target'] == expected['target']]
        if (len(actual) != 1 or actual[0]['type'] != expected['type']
                or actual[0].get('read_only', False) != expected.get('read_only', False)
                or actual[0].get('source') != expected.get('source')):
            raise ValueError(f'Missing required shared protection: {expected["target"]}')
        if not expected.get('read_only', False):
            continue
        for mount in mounts:
            if mount['target'] != expected['target'] and Path(mount['target']).is_relative_to(expected['target']):
                # The generated secret masks inside .devcontainer are allowed.
                if mount['target'] not in protected_targets:
                    raise ValueError(f'Mount overrides shared protection: {mount["target"]}')


def generate(root=ROOT, environ=os.environ):
    """Check the settings and the files they name, and write .generated/."""
    outside = outside_settings(root, environ)
    if outside:
        raise ValueError(
            'the settings come only from global claude.toml, .devcontainer/claude.toml and claude.local.toml, but found '
            + ', '.join(outside) + '. Move them there. Inside the agent, compose sets some of them; '
            'run with them removed, as `env -u CLAUDE_DEVCONTAINER_DIND -u CLAUDE_DEVCONTAINER_NIX '
            '-u CLAUDE_DEVCONTAINER_SUMI devcontainer up`.')
    settings = load(root)
    path_script = shell_path(settings['agent']['extra_path'])
    workspace = workspace_compose(settings, root, environ)
    secrets = settings['sumi']['secrets']
    if settings['sumi']['enabled'] and not secrets.is_file():
        raise ValueError(f'sumi.enabled needs {secrets} (sumi.secrets), one secret per line, with mode 0600.')
    ca = settings['proxy']['ca']
    if ca and not ca.is_file():
        raise ValueError(f'proxy.ca names {ca}, which is not a file.')
    out = layout.config_dir(root) / '.generated'
    out.mkdir(parents=True, exist_ok=True)
    (out / '.gitignore').write_text('*\n')
    if settings['git']['isolation'] in SHARED_MODES:
        write(out / 'empty-secret', '')
        (out / 'empty-secret').chmod(0o444)
        write(out / 'hidden.txt', ''.join(p.as_posix() + '\n' for p in hidden_paths(settings, root)))
    header = '# Written by scripts/settings.py from global and workspace claude.toml and claude.local.toml; do not edit.\n'
    values = variables(settings)
    write(out / 'settings.env', header + ''.join(f"{name}='{value}'\n" for name, value in values.items()))
    write(out / 'proxy.json', proxy_json(settings))
    write(out / 'images.txt', images_txt(settings))
    write(out / 'agent.env', agent_env(settings, header))
    global_setup = CONFIG / 'agent-setup.sh'
    if global_setup.is_file():
        write(out / 'global-agent-setup.sh', global_setup.read_text())
    else:
        (out / 'global-agent-setup.sh').unlink(missing_ok=True)
    write(out / 'workspace.compose.yaml', workspace)
    write(out / 'shell-path.sh', path_script)
    return settings


def shell_path(paths, source='agent.extra_path'):
    """Resolve only configured directories on the host and prepend them in Bash."""
    resolved = []
    for value in paths:
        path = Path(value).expanduser()
        if not path.is_absolute() or any(char in value for char in ':\n\r'):
            raise ValueError(f'{source}: agent.extra_path: expected an absolute path or ~/path without colons or newlines')
        # ~/.nix-profile/bin points into the store mounted at the same path.
        # Paths that only exist in the container are allowed too.
        directory = str(path.resolve())
        if any(char in directory for char in ':\n\r'):
            raise ValueError(f'{source}: agent.extra_path: resolved path contains colons or newlines')
        if directory not in resolved:
            resolved.append(directory)
    if not resolved:
        return ''
    return 'export PATH=' + shlex.quote(':'.join(resolved)) + '"${PATH:+:$PATH}"\n'


def main():
    try:
        if sys.argv[1:] == ['generate']:
            generate()
        elif sys.argv[1:] == ['prepare-dependencies']:
            settings = load()
            prepare_dind(settings)
            prepare_cow_mounts(settings)
        elif sys.argv[1:] == ['check-shared-mounts']:
            check_shared_mounts()
        elif len(sys.argv) == 3 and sys.argv[1] == 'proxy-json':
            path = Path(sys.argv[2])
            data = read(path)
            check(data, path.name)
            print(proxy_json(merge(copy.deepcopy(DEFAULTS), data)), end='')
        else:
            sys.exit('usage: settings.py generate | prepare-dependencies | check-shared-mounts | proxy-json FILE')
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        sys.exit(f'error: {error}')


if __name__ == '__main__':
    main()
