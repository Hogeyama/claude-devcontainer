"""Fixed outbound policy. Only the host-built image can change these rules."""
import base64
import json
import logging
import re
from urllib.parse import unquote, urlsplit

from mitmproxy import http
from credentials import read_snapshot

LOG = logging.getLogger('policy')
HOSTS = {'api.github.com', 'github.com', 'api.anthropic.com'}
MAX_BODY = 8 * 1024 * 1024
# Claude Code's startup connectivity check.
HELLO = ('api.anthropic.com', '/api/hello')
NO_CREDENTIALS = 'no credentials; run .devcontainer/claude/scripts/inject-credentials.py on the host'
# Everything else, including any client-supplied credential, is dropped.
SAFE_HEADERS = {'accept', 'accept-encoding', 'accept-language', 'content-type', 'content-length',
                'transfer-encoding', 'user-agent', 'git-protocol', 'if-match', 'if-none-match',
                'if-modified-since', 'if-unmodified-since', 'range', 'anthropic-beta',
                'x-github-api-version'}


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
        # A URL or file source makes Anthropic's servers fetch it.
        source = block.get('source')
        require(not isinstance(source, dict) or source.get('type') in ('base64', 'text'))
        if block.get('type') == 'tool_result':
            check_blocks(block.get('content', ''))


def check_messages(raw):
    """Reject the ways a request can make Anthropic's servers contact other hosts."""
    body = json.loads(raw, object_pairs_hook=unique_keys)
    require(not {'mcp_servers', 'container'} & set(body))
    for tool in body.get('tools', []):
        require(tool.get('type', 'custom') == 'custom')  # Server tools: web fetch, code execution, ...
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
    if req.host == 'api.github.com':
        require((req.method, path) in {('POST', '/graphql'), ('GET', '/user')}
                or re.fullmatch(r'/repos/[^/]+/[^/]+(/.*)?', path))
    elif req.host == 'github.com':
        require(re.fullmatch(r'/[^/]+/[^/]+\.git/(info/refs|git-upload-pack|git-receive-pack)', path))
    elif (req.method, path) == ('GET', '/api/hello'):
        pass  # Claude Code's startup connectivity check.
    else:
        require(req.method == 'POST' and path in ('/v1/messages', '/v1/messages/count_tokens'))
        # A declared bound lets mitmproxy buffer the body for inspection.
        length = req.headers.get('content-length', '')
        require(length.isdecimal() and int(length) <= MAX_BODY and 'transfer-encoding' not in req.headers)
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
        # instead; except for Anthropic bodies, which check_request bounded, so
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
                                    if name.decode('ascii').lower() in SAFE_HEADERS])
        req.headers['Host'] = req.host
        if (req.host, path) == HELLO:
            return  # Needs no credentials, so Claude Code starts before they are injected.
        try:
            github_token, anthropic_token, auth_mode = read_snapshot()
        except Exception:
            return self.deny(flow, NO_CREDENTIALS)
        try:
            if req.host == 'api.anthropic.com':
                req.headers['anthropic-version'] = '2023-06-01'
                if auth_mode == 'api-key':
                    req.headers['x-api-key'] = anthropic_token
                else:
                    req.headers['Authorization'] = 'Bearer ' + anthropic_token
                    betas = [beta.strip() for beta in req.headers.get('anthropic-beta', '').split(',') if beta.strip()]
                    req.headers['anthropic-beta'] = ','.join(dict.fromkeys(betas + ['oauth-2025-04-20']))
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
