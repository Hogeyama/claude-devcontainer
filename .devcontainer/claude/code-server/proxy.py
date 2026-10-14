"""Trusted browser-side allowlist. Agent/code-server cannot edit this policy."""
import json
import re
from mitmproxy import http, ctx

BACKEND = ('agent-code-server', 8088)
PORT_HOST = re.compile(r'port-([1-9][0-9]{0,4})\.localhost')


def target_for(host, port):
    # Never resolve a browser-selected host or connect to its selected port.
    # code-server interprets the Host and forwards inside the agent only.
    if port != 8000:
        return None
    if host == 'ide.localhost':
        return BACKEND
    match = PORT_HOST.fullmatch(host)
    if match and 1 <= int(match[1]) <= 65535 and int(match[1]) != 8088:
        return BACKEND
    return None


def block(flow):
    ctx.log.warn('CODE_SERVER_BLOCK ' + json.dumps({
        'method': flow.request.method, 'url': flow.request.pretty_url,
    }))
    flow.response = http.Response.make(451,
        b'<!doctype html><title>Blocked by code-server proxy</title><h1>Blocked by code-server proxy</h1><p>This destination is outside the browser allowlist.</p>',
        {'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store'})


def http_connect(flow):
    # Chromium tunnels even ws:// through CONNECT. Every allowed name still
    # connects to the single code-server backend, never a host-chosen address.
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
    # Normalize Host from the validated URL/authority, not a conflicting header.
    host_header = f'{flow.request.host}:8000'
    flow.request.host, flow.request.port = target
    flow.request.headers.pop('forwarded', None)
    flow.request.headers.pop('x-forwarded-host', None)
    flow.request.headers['host'] = host_header
    # Keep the original Host/Origin pair for code-server's websocket checks.
    ctx.log.info('CODE_SERVER_ALLOW ' + flow.request.method + ' ' + flow.request.path)
