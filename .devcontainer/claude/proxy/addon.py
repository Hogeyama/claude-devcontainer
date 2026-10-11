"""Fixed outbound policy. Only the host-built image can change these rules."""
import base64
import json
import logging
import os
import re
from urllib.parse import unquote, urlsplit

from mitmproxy import http
from credentials import read_snapshot
import workspace_config

# --- Policy -------------------------------------------------------------------

# The only requests let through, by host: (method, path regex). None allows
# any method. Every host is reached on port 443 over HTTPS only.
ROUTES = {
    'api.github.com': [
        ('POST', r'/graphql'),
        ('GET', r'/user'),
        (None, r'/repos/[^/]+/[^/]+(/.*)?'),
    ],
    'github.com': [
        (None, r'/[^/]+/[^/]+\.git/(info/refs|git-upload-pack|git-receive-pack)'),
    ],
    'api.anthropic.com': [
        ('GET', r'/api/hello'),  # HELLO below
        ('POST', r'/v1/messages'),
        ('POST', r'/v1/messages/count_tokens'),
        # Remote managed settings. With an OAuth token, Claude Code warns when
        # it cannot read them.
        ('GET', r'/api/claude_code/settings'),
        # Feature flags (GrowthBook remote evaluation, POST /api/eval/<client
        # key>). The body carries account and organization attributes. Without
        # it every flag keeps its default, which turns off Artifact comments and
        # live updates.
        ('POST', r'/api/eval/[^/]+'),
        # Artifacts: the Artifact tool publishes, reads and lists through
        # /api/frame/; the claude.ai MCP server list names the Artifacts MCP
        # server; the organization policy and the account's plan gate both.
        # Except /api/frame/perm/, which changes who can open an artifact: the
        # agent could otherwise make one public and hand its content to anyone.
        # Sharing is left to the person, on claude.ai.
        (None, r'/api/frame/(?!(?i:perm)(/|$)).+'),
        ('GET', r'/v1/mcp_servers'),
        ('GET', r'/api/claude_code/policy_limits'),
        ('GET', r'/api/oauth/profile'),
        (None, r'/api/artifacts/mcp(/upload/.+)?'),
    ],
}


def connector_routes(value):
    """Routes to the claude.ai connectors in CLAUDE_DEVCONTAINER_CONNECTORS.

    Claude Code reaches a connector through mcp-proxy.anthropic.com with the
    claude.ai token, and Anthropic's servers then act on the connected account
    (Drive, Gmail, ...). Only the connectors named here are let through. The
    IDs come from GET /v1/mcp_servers as mcpsrv_..., which the path carries;
    mcprs_... spells the same server elsewhere, so both are accepted.
    """
    ids = []
    for item in value.replace(',', ' ').split():
        if not re.fullmatch(r'mcp(srv|rs)_[A-Za-z0-9]+', item):
            raise ValueError(f'CLAUDE_DEVCONTAINER_CONNECTORS: {item!r} is not a connector ID (mcpsrv_...)')
        ids.append(item.split('_', 1)[1])
    if not ids:
        return {}
    return {'mcp-proxy.anthropic.com': [(None, r'/v1/mcp/mcp(srv|rs)_(%s)(/.*)?' % '|'.join(ids))]}


ROUTES.update(connector_routes(os.environ.get('CLAUDE_DEVCONTAINER_CONNECTORS', '')))
# Only these get credentials. The workspace's routes go to other hosts.
assert set(ROUTES) <= workspace_config.AUTHENTICATED_HOSTS
ROUTES.update(workspace_config.routes())
HOSTS = set(ROUTES)
# Claude Code's startup connectivity check. It needs no credentials, so Claude
# Code starts before they are injected.
HELLO = ('api.anthropic.com', '/api/hello')

# Top-level fields that make Anthropic's servers contact other hosts.
DENIED_FIELDS = {'mcp_servers', 'container'}
# Tools that run in the client. Any other type (web fetch, code execution, ...)
# runs on Anthropic's servers. A tool without a type is a custom tool.
CLIENT_TOOL_TYPES = {'custom'}
# Server tools checked to only run a model on Anthropic's servers and reach no
# other host. Claude Code adds the advisor for subscriptions. Each version is
# listed: a new one may do more, and is denied (and logged) until checked.
MODEL_TOOL_TYPES = {'advisor_20260301'}
# Content sources carried in the request. A URL or file source makes
# Anthropic's servers fetch it.
INLINE_SOURCES = {'base64', 'text'}

# Request headers passed upstream. Everything else, including any
# client-supplied credential, is dropped.
SAFE_HEADERS = {'accept', 'accept-encoding', 'accept-language', 'content-type', 'content-length',
                'transfer-encoding', 'user-agent', 'git-protocol', 'if-match', 'if-none-match',
                'if-modified-since', 'if-unmodified-since', 'range', 'anthropic-beta',
                'x-github-api-version', 'mcp-session-id', 'mcp-protocol-version', 'last-event-id',
                'x-mcp-server-id', 'x-mcp-client-session-id', 'mcp-method', 'mcp-name', 'x-upload-token',
                'anthropic-mcp-client-capabilities', 'anthropic-mcp-discover-protocol-version'}
# The Artifact tool's headers. Anthropic routes /api/frame/ requests by them
# (without X-Frame-CP the answer is 404), and new ones keep being added.
SAFE_HEADER_PREFIXES = ('x-frame-',)
ANTHROPIC_VERSION = '2023-06-01'
OAUTH_BETA = 'oauth-2025-04-20'

NO_CREDENTIALS = 'no credentials; run .devcontainer/claude/scripts/inject-credentials.py on the host'

# ------------------------------------------------------------------------------

LOG = logging.getLogger('policy')


class Denied(Exception):
    pass


def require(condition, reason):
    """Deny unless condition; reason goes to the log, so it must not carry request content."""
    if not condition:
        raise Denied(reason)


def unique_keys(pairs):
    # A duplicate key could hide a field from this check but not from Anthropic.
    require(len({key for key, _ in pairs}) == len(pairs), 'duplicate key')
    return dict(pairs)


def check_blocks(content):
    if isinstance(content, str):
        return
    for block in content:
        source = block.get('source')
        if isinstance(source, dict):
            require(source.get('type') in INLINE_SOURCES, f'source type {str(source.get("type"))[:40]}')
        if block.get('type') == 'tool_result':
            check_blocks(block.get('content', ''))


def check_messages(raw):
    """Reject the ways a request can make Anthropic's servers contact other hosts."""
    body = json.loads(raw, object_pairs_hook=unique_keys)
    require(not DENIED_FIELDS & set(body), 'field ' + ','.join(sorted(DENIED_FIELDS & set(body))))
    for tool in body.get('tools', []):
        kind = tool.get('type', 'custom')
        require(kind in CLIENT_TOOL_TYPES | MODEL_TOOL_TYPES, f'tool type {str(kind)[:40]}')
    for message in body['messages']:
        check_blocks(message['content'])
    check_blocks(body.get('system', ''))


def check_request(req):
    """Check what is known from the headers and return the URL path."""
    require(req.scheme == 'https' and req.host in HOSTS and req.port == 443, 'host')
    require('upgrade' not in req.headers, 'upgrade')
    path = urlsplit(req.path).path
    decoded = unquote(path)
    # GitHub may normalize what this check sees as a /repos/... path.
    require(not re.search(r'[\x00-\x1f\x7f%\\]', decoded) and '//' not in decoded
            and not {'.', '..'} & set(decoded.split('/')), 'path spelling')
    # Both spellings must match, or an encoded segment (%70erm for perm) could
    # slip past a pattern that excludes it.
    require(any(method in (None, req.method) and re.fullmatch(pattern, path) and re.fullmatch(pattern, decoded)
                for method, pattern in ROUTES[req.host]), 'route')
    if req.host == 'api.anthropic.com':
        # check_messages reads the body as sent.
        require(req.headers.get('content-encoding', 'identity') == 'identity', 'content-encoding')
    return path


class PolicyAddon:
    def deny(self, flow, reason='request policy', error=None):
        # The query is left out: it is the likeliest part of a URL to hold a secret.
        req = flow.request
        detail = str(error) if isinstance(error, Denied) else type(error).__name__ if error else reason
        LOG.warning('request-denied %s %s%s (%s)', req.method, req.pretty_host, urlsplit(req.path).path, detail)
        flow.response = http.Response.make(403, b'blocked: ' + reason.encode(), {'Content-Type': 'text/plain'})
        flow.metadata['denied'] = True

    def http_connect(self, flow):
        if flow.request.host not in HOSTS or flow.request.port != 443:
            self.deny(flow, error=Denied('host'))

    def requestheaders(self, flow):
        req = flow.request
        try:
            path = check_request(req)
        except Exception as error:
            self.deny(flow, error=error)
            path = None
        if path is not None and req.host in workspace_config.AUTHENTICATED_HOSTS and (req.host, path) != HELLO:
            try:
                read_snapshot()
            except Exception:
                self.deny(flow, NO_CREDENTIALS)
        # mitmproxy reads the whole body before sending a response, so close
        # instead; except for Anthropic bodies, which are read whole anyway, so
        # that Claude can show the reason.
        bounded = path is not None and req.host == 'api.anthropic.com'
        body = req.headers.get('content-length', '0') != '0' or 'transfer-encoding' in req.headers
        if flow.response is not None and body and not bounded:
            flow.kill()

    def request(self, flow):
        if flow.response is not None:
            return
        req = flow.request
        try:
            path = check_request(req)
            if path.startswith('/v1/messages'):
                check_messages(req.raw_content)
        except Exception as error:
            return self.deny(flow, error=error)
        req.headers = http.Headers([(name, value) for name, value in req.headers.fields
                                    if name.decode('ascii').lower() in SAFE_HEADERS
                                    or name.decode('ascii').lower().startswith(SAFE_HEADER_PREFIXES)])
        req.headers['Host'] = req.host
        if req.host not in workspace_config.AUTHENTICATED_HOSTS or (req.host, path) == HELLO:
            return
        try:
            github_token, anthropic_token, auth_mode = read_snapshot()
        except Exception:
            return self.deny(flow, NO_CREDENTIALS)
        try:
            if req.host == 'api.anthropic.com':
                req.headers['anthropic-version'] = ANTHROPIC_VERSION
                if auth_mode == 'api-key':
                    req.headers['x-api-key'] = anthropic_token
                else:
                    req.headers['Authorization'] = 'Bearer ' + anthropic_token
                    betas = [beta.strip() for beta in req.headers.get('anthropic-beta', '').split(',') if beta.strip()]
                    req.headers['anthropic-beta'] = ','.join(dict.fromkeys(betas + [OAUTH_BETA]))
            elif req.host == 'mcp-proxy.anthropic.com':
                # Connectors belong to the claude.ai account; an API key has none.
                require(auth_mode == 'oauth', 'connectors need a claude.ai login')
                req.headers['Authorization'] = 'Bearer ' + anthropic_token
            elif req.host == 'api.github.com':
                req.headers['Authorization'] = 'Bearer ' + github_token
            elif req.host == 'github.com':
                req.headers['Authorization'] = 'Basic ' + base64.b64encode(('x-access-token:' + github_token).encode()).decode()
        except Exception as error:
            self.deny(flow, error=error)

    def server_connect(self, data):
        # mitmproxy logs and ignores hook exceptions, so this must not raise.
        address = data.server.address
        if address is None or address[0] not in HOSTS or address[1] != 443:
            data.server.error = 'upstream-denied'

    def responseheaders(self, flow):
        # Responses are not inspected; streaming keeps server-sent events live.
        flow.response.stream = True
        if flow.metadata.get('denied'):
            return
        # As with denials, the query is left out.
        req, res = flow.request, flow.response
        LOG.warning('upstream %d %s %s%s %s', res.status_code, req.method, req.pretty_host,
                    urlsplit(req.path).path, res.headers.get('request-id', '-'))


addons = [PolicyAddon()]
