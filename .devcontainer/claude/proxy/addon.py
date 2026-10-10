"""Fixed outbound policy. Only the host-built image can change these rules."""
import base64
import json
import logging
import re
from urllib.parse import unquote, urlsplit

from mitmproxy import http
from credentials import read_snapshot

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
        # Artifacts: the Artifact tool publishes, reads and lists through
        # /api/frame/; the claude.ai MCP server list names the Artifacts MCP
        # server; the organization policy and the account's plan gate both.
        (None, r'/api/frame/.+'),
        ('GET', r'/v1/mcp_servers'),
        ('GET', r'/api/claude_code/policy_limits'),
        ('GET', r'/api/oauth/profile'),
        (None, r'/api/artifacts/mcp(/upload/.+)?'),
    ],
}
HOSTS = set(ROUTES)
# Claude Code's startup connectivity check. It needs no credentials, so Claude
# Code starts before they are injected.
HELLO = ('api.anthropic.com', '/api/hello')

# Top-level fields that make Anthropic's servers contact other hosts.
DENIED_FIELDS = {'mcp_servers', 'container'}
# Tools that run in the client. Any other type (web fetch, code execution, ...)
# runs on Anthropic's servers. A tool without a type is a custom tool.
CLIENT_TOOL_TYPES = {'custom'}
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


def require(condition):
    if not condition:
        raise Denied()


def unique_keys(pairs):
    # A duplicate key could hide a field from this check but not from Anthropic.
    require(len({key for key, _ in pairs}) == len(pairs))
    return dict(pairs)


def check_blocks(content):
    if isinstance(content, str):
        return
    for block in content:
        source = block.get('source')
        require(not isinstance(source, dict) or source.get('type') in INLINE_SOURCES)
        if block.get('type') == 'tool_result':
            check_blocks(block.get('content', ''))


def check_messages(raw):
    """Reject the ways a request can make Anthropic's servers contact other hosts."""
    body = json.loads(raw, object_pairs_hook=unique_keys)
    require(not DENIED_FIELDS & set(body))
    for tool in body.get('tools', []):
        require(tool.get('type', 'custom') in CLIENT_TOOL_TYPES)
    for message in body['messages']:
        check_blocks(message['content'])
    check_blocks(body.get('system', ''))


def check_request(req):
    """Check what is known from the headers and return the URL path."""
    require(req.scheme == 'https' and req.host in HOSTS and req.port == 443)
    require('upgrade' not in req.headers)
    path = urlsplit(req.path).path
    decoded = unquote(path)
    # GitHub may normalize what this check sees as a /repos/... path.
    require(not re.search(r'[\x00-\x1f\x7f%\\]', decoded) and '//' not in decoded
            and not {'.', '..'} & set(decoded.split('/')))
    require(any(method in (None, req.method) and re.fullmatch(pattern, path)
                for method, pattern in ROUTES[req.host]))
    if req.host == 'api.anthropic.com':
        # check_messages reads the body as sent.
        require(req.headers.get('content-encoding', 'identity') == 'identity')
    return path


class PolicyAddon:
    def deny(self, flow, reason='request policy'):
        # The query is left out: it is the likeliest part of a URL to hold a secret.
        req = flow.request
        LOG.warning('request-denied %s %s%s', req.method, req.pretty_host, urlsplit(req.path).path)
        flow.response = http.Response.make(403, b'blocked: ' + reason.encode(), {'Content-Type': 'text/plain'})

    def http_connect(self, flow):
        if flow.request.host not in HOSTS or flow.request.port != 443:
            self.deny(flow)

    def requestheaders(self, flow):
        req = flow.request
        try:
            path = check_request(req)
        except Exception:
            self.deny(flow)
            path = None
        if path is not None and (req.host, path) != HELLO:
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
        except Exception:
            return self.deny(flow)
        req.headers = http.Headers([(name, value) for name, value in req.headers.fields
                                    if name.decode('ascii').lower() in SAFE_HEADERS
                                    or name.decode('ascii').lower().startswith(SAFE_HEADER_PREFIXES)])
        req.headers['Host'] = req.host
        if (req.host, path) == HELLO:
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
            elif req.host == 'api.github.com':
                req.headers['Authorization'] = 'Bearer ' + github_token
            else:
                req.headers['Authorization'] = 'Basic ' + base64.b64encode(('x-access-token:' + github_token).encode()).decode()
        except Exception:
            self.deny(flow)

    def server_connect(self, data):
        # mitmproxy logs and ignores hook exceptions, so this must not raise.
        address = data.server.address
        if address is None or address[0] not in HOSTS or address[1] != 443:
            data.server.error = 'upstream-denied'

    def responseheaders(self, flow):
        # Responses are not inspected; streaming keeps server-sent events live.
        flow.response.stream = True


addons = [PolicyAddon()]
