"""A request to run one command, as the container sends it.

Everything the command gets, argv, working directory and environment, is in
the request, so the person approving it sees exactly what will run.

Adapted from strait (nix-agent-sandbox/contrib/strait, MIT).
"""
from dataclasses import dataclass, field
import json
import posixpath
import re

MAX_BODY = 1024 * 1024

ENV_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')


@dataclass(frozen=True)
class ExecRequest:
    argv: list
    # On the host, after the mapping.
    cwd: str
    # As the container sent it.
    requested_cwd: str
    # Added to the inherited PATH and HOME, overriding them.
    env: dict = field(default_factory=dict)


class Invalid(ValueError):
    """Why a request, or a mapping, is not valid."""


class _Duplicate(Exception):
    pass


def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise _Duplicate
        out[key] = value
    return out


def _text(value):
    """A string that is what would run: no NUL, which exec cannot take, and
    nothing (a lone surrogate) that has no UTF-8 spelling."""
    if not isinstance(value, str) or '\0' in value:
        return False
    try:
        value.encode('utf-8')
    except UnicodeEncodeError:
        return False
    return True


def normalize(path):
    """An absolute path with `.`, `..` and repeated slashes resolved."""
    p = posixpath.normpath(path)
    # POSIX keeps a leading `//`; nothing here means anything by it.
    return '/' + p.lstrip('/')


def parse_exec_request(body, mapping=()):
    """The request in `body` (bytes), or Invalid saying why it is not one."""
    try:
        raw = json.loads(body.decode('utf-8'), object_pairs_hook=_no_duplicates)
    except _Duplicate:
        raise Invalid('body has a duplicated member') from None
    except ValueError:
        raise Invalid('body is not JSON') from None
    if not isinstance(raw, dict):
        raise Invalid('body is not an object')
    for key in raw:
        if key not in ('argv', 'cwd', 'env'):
            raise Invalid(f'body has an unexpected member {json.dumps(key)}')
    argv, cwd, env = raw.get('argv'), raw.get('cwd'), raw.get('env', {})
    if not isinstance(argv, list) or not argv or not all(_text(a) for a in argv):
        raise Invalid('argv must be a non-empty array of strings')
    if argv[0] == '':
        raise Invalid('argv[0] is empty')
    if not _text(cwd) or not cwd.startswith('/'):
        raise Invalid('cwd must be an absolute path')
    # No env: no variables beyond PATH and HOME.
    if not isinstance(env, dict):
        raise Invalid('env must be an object')
    for name, value in env.items():
        if not ENV_NAME.fullmatch(name):
            raise Invalid(f'env name {json.dumps(name)} is not valid')
        if not _text(value):
            raise Invalid(f'env {name} must be a string')
    return ExecRequest(argv=argv, cwd=map_path(cwd, mapping), requested_cwd=cwd, env=env)


def map_path(path, mapping):
    """The host path for a container path: the longest matching prefix wins,
    and a path no prefix covers is kept as it is. `..` is resolved first, so a
    mapped path cannot climb out of its host directory by its spelling."""
    p = normalize(path)
    best = None
    for entry in mapping:
        prefix = entry[0]
        covers = p == prefix or p.startswith('/' if prefix == '/' else prefix + '/')
        if covers and (best is None or len(prefix) > len(best[0])):
            best = entry
    if best is None:
        return p
    rest = p[len(best[0]):].lstrip('/')
    return best[1] if rest == '' else best[1].rstrip('/') + '/' + rest


def parse_mapping(spec):
    """`CONTAINER=HOST`, from --map or the proxy's X-Hostexec-Map."""
    source, eq, target = spec.partition('=')
    if not eq:
        raise Invalid(f'{spec}: expected CONTAINER=HOST')
    if not source.startswith('/') or not target.startswith('/') or '\0' in spec:
        raise Invalid(f'{spec}: both paths must be absolute')
    return normalize(source), normalize(target)
