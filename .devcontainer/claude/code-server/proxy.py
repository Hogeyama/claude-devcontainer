"""Trusted browser-side allowlist. The agent cannot edit this policy.

The editor's shell (shell/) sends every request here, for one of the origins
http://localhost:<port>, and this maps each port to the agent:
  18000  the IDE and its webviews, served by Coder code-server
  other  the agent's web app on the same port
The agent's firewall forwards the connection to its loopback, where they
listen (agent/init-firewall.sh). Any other host, scheme or port is refused,
and so is a redirect to one.

The IDE's responses pass as they are. Those of the apps
lose X-Frame-Options and the CSP's frame-ancestors, so that they show in VS
Code's Simple Browser, an iframe of the IDE's site: an app in the agent is
the attacker's to change anyway.
"""
import json
import re
from urllib.parse import urljoin, urlsplit

from mitmproxy import http, ctx

AGENT = 'agent-code-server'
IDE_PORT = 18000
# The port the shell asked for, which the request no longer names once rewritten.
PORT = 'claude-devcontainer-port'
BLOCKED_PAGE = (
    b'<!doctype html><title>Blocked by code-server proxy</title>'
    b'<h1>Blocked by code-server proxy</h1>'
    b'<p>This destination is outside the browser allowlist.</p>'
)


def target_for(host, port):
    # Never resolve a browser-selected host or connect to an address it chose.
    if host != 'localhost' or not 1 <= port <= 65535:
        return None
    return AGENT, port


def allows_location(location, port):
    """A redirect to one of the origins, given relative to the one that answered."""
    try:
        target = urlsplit(urljoin(f'http://localhost:{port}/', location))
        return target.scheme == 'http' and target_for(target.hostname, target.port or 80) is not None
    except ValueError:
        return False


def frame_ancestors(value):
    """Remove frame-ancestors from each policy so apps can appear in the IDE."""
    policies = []
    for policy in value.split(','):
        directives = [d.strip() for d in policy.split(';') if d.strip()]
        kept = [d for d in directives if d.split()[0].lower() != 'frame-ancestors']
        if kept:
            policies.append('; '.join(kept))
    return ', '.join(policies)


def block(flow):
    ctx.log.warn('CODE_SERVER_BLOCK ' + json.dumps({
        'method': flow.request.method, 'url': flow.request.pretty_url,
    }))
    flow.response = http.Response.make(
        451, BLOCKED_PAGE, {'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store'})


def http_connect(flow):
    # Chromium tunnels even ws:// through CONNECT; the tunnel goes to the
    # agent like a request to the same origin.
    target = target_for(flow.request.host, flow.request.port)
    if target is None:
        block(flow)
    else:
        flow.server_conn.address = target


def request(flow):
    target = target_for(flow.request.host, flow.request.port)
    if flow.request.scheme != 'http' or target is None:
        block(flow)
        return
    # Keep apps at their own origin, where absolute paths and login cookies work.
    # Both code-server proxy routes return to the app's own origin here.
    if flow.request.port == IDE_PORT and flow.request.path.startswith(('/proxy/', '/absproxy/')):
        # The query can follow the port directly, without a slash or app path.
        match = re.fullmatch(r'/(?:proxy|absproxy)/([0-9]{1,5})(/[^?\r\n]*)?(\?[^\r\n]*)?', flow.request.path)
        if match is None:
            block(flow)
            return
        app_port, app_path, query = match.groups()
        if not 1 <= int(app_port) <= 65535:
            block(flow)
            return
        location = f'http://localhost:{int(app_port)}{app_path or "/"}{query or ""}'
        flow.response = http.Response.make(
            302, b'', {'Location': location, 'Cache-Control': 'no-store'})
        return
    # The Host the app sees in the agent too, not a conflicting header.
    flow.metadata[PORT] = port = flow.request.port
    flow.request.host, flow.request.port = target
    flow.request.headers.pop('forwarded', None)
    flow.request.headers.pop('x-forwarded-host', None)
    flow.request.headers['host'] = f'localhost:{port}'
    ctx.log.info(f'CODE_SERVER_ALLOW {flow.request.method} localhost:{port}{flow.request.path}')


def response(flow):
    port = flow.metadata.get(PORT)
    if port is None or flow.response is None:
        return
    location = flow.response.headers.get('location')
    if location is not None and not allows_location(location, port):
        block(flow)
        return
    if port == IDE_PORT:
        return
    flow.response.headers.pop('x-frame-options', None)
    policies = [frame_ancestors(value) for value in flow.response.headers.get_all('content-security-policy')]
    flow.response.headers.set_all('content-security-policy', [policy for policy in policies if policy])
