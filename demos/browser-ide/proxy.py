"""Trusted browser-side allowlist. Agent/code-server cannot edit this policy."""
import json
import os
from mitmproxy import http, ctx

ROUTES = {
    ('ide.localhost', 8000): ('ide', 8080),
    ('preview.localhost', 8001): ('preview', 8001),
}
if os.environ.get('DEMO_CONTROL') == '1':
    # Positive control: only a disposable local receiver, never the Internet.
    ROUTES[('receiver.localhost', 9000)] = ('receiver', 9000)


def block(flow):
    ctx.log.warn('DEMO_BLOCK ' + json.dumps({
        'method': flow.request.method, 'url': flow.request.pretty_url,
    }))
    flow.response = http.Response.make(451,
        b'<!doctype html><title>Blocked by demo proxy</title><h1>Blocked by demo proxy</h1><p>This destination is outside the browser allowlist.</p>',
        {'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store'})


def http_connect(flow):
    # Chromium tunnels even ws:// through CONNECT. Only fixed demo backends
    # are reachable; ordinary HTTPS/external CONNECT destinations are denied.
    target = ROUTES.get((flow.request.host, flow.request.port))
    if target is None:
        block(flow)
    else:
        flow.server_conn.address = target


def request(flow):
    target = ROUTES.get((flow.request.host, flow.request.port))
    if flow.request.scheme != 'http' or target is None:
        block(flow)
        return
    host_header = flow.request.headers.get('host')
    flow.request.host, flow.request.port = target
    if host_header:
        flow.request.headers['host'] = host_header
    # Keep the original Host/Origin pair for code-server's websocket checks.
    ctx.log.info('DEMO_ALLOW ' + flow.request.method + ' ' + flow.request.path)
