"""The server the container reaches, through its proxy, as
https://host.docker.internal:PORT.

The proxy adds the API token, which the container never sees, so a request
without it came some other way (another container on the same bridge, say)
and is refused.

  POST /run     {argv, cwd, env}  -> NDJSON events, see `serve_run`
                X-Hostexec-Map: CONTAINER=HOST, which the proxy adds as well,
                says where this workspace's container paths are on the host
  GET  /client                    -> the Python client, to install in the container
"""
import base64
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import queue
import select
import ssl
import time

from . import approvals as decisions
from .request import MAX_BODY, Invalid, parse_exec_request, parse_mapping
from .run import Run

MAP_HEADER = 'X-Hostexec-Map'
KEEPALIVE_SECONDS = 10
# Only ends dead connections: keepalives keep a held request busy.
IDLE_SECONDS = 60


def same_secret(given, want):
    if given is None:
        return False
    digest = lambda s: hashlib.sha256(s.encode('utf-8', 'surrogateescape')).digest()
    return hmac.compare_digest(digest(given), digest(want))


class Server(ThreadingHTTPServer):
    """One thread per connection, with the TLS handshake in that thread so
    that a slow client holds up no one else."""
    daemon_threads = True

    def __init__(self, address, handler, tls=None):
        self.tls = tls
        super().__init__(address, handler)

    def finish_request(self, request, client_address):
        if self.tls is not None:
            request.settimeout(IDLE_SECONDS)
            try:
                request = self.tls.wrap_socket(request, server_side=True)
            except (OSError, ssl.SSLError):
                request.close()
                return
        super().finish_request(request, client_address)


class Handler(BaseHTTPRequestHandler):
    """Base for both servers: one request per connection, nothing logged."""
    protocol_version = 'HTTP/1.1'
    server_version = 'hostexec'
    sys_version = ''
    timeout = IDLE_SECONDS

    def log_message(self, format, *args):
        pass

    def reply(self, status, body, content_type, headers=()):
        data = body.encode()
        self.close_connection = True
        self.send_response(status)
        self.send_header('Content-Type', f'{content_type}; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        for name, value in headers:
            self.send_header(name, value)
        self.send_header('Connection', 'close')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def body(self, limit):
        """The body, of at most `limit` bytes, or None if it is not one this
        reads: encoded, chunked, or without a Content-Length."""
        if self.headers.get('Content-Encoding') is not None or \
                self.headers.get('Transfer-Encoding') is not None:
            return None
        length = self.headers.get('Content-Length')
        if length is None:
            return b''
        if not length.isdigit() or int(length) > limit:
            return None
        return self.rfile.read(int(length))

    def do_any(self):
        raise NotImplementedError

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = \
        lambda self: self.do_any()


def api_handler(token, approvals, mapping, secrets, client, keepalive=KEEPALIVE_SECONDS):
    """The request handler for the container's API."""

    class ApiHandler(Handler):
        def text(self, status, message):
            self.reply(status, message + '\n', 'text/plain')

        def do_any(self):
            if not same_secret(self.headers.get('Authorization'), f'Bearer {token}'):
                return self.text(401, 'hostexec: no or wrong token (is the request going through the proxy?)')
            path, query, _ = self.path.partition('?')
            if query:
                return self.text(400, 'hostexec: no query is taken')
            if self.command == 'GET' and path == '/client':
                return self.reply(200, client, 'text/x-python')
            if path != '/run':
                return self.text(404, 'hostexec: not found')
            if self.command != 'POST':
                return self.text(405, 'hostexec: POST /run only')
            body = self.body(MAX_BODY) if 'Content-Length' in self.headers else None
            if body is None:
                return self.text(400, f'hostexec: the body must have a Content-Length up to {MAX_BODY}, '
                                      'and no encoding')
            # The proxy's mapping comes first, so it wins over a --map of the
            # same container path: it names the workspace this request came from.
            request_mapping = list(mapping)
            header = self.headers.get(MAP_HEADER)
            if header is not None:
                # http.server reads a header as Latin-1: a path in anything
                # but ASCII would come out as some other path.
                if not all(' ' <= c <= '~' for c in header):
                    return self.text(400, f'hostexec: {MAP_HEADER}: the workspace path must be printable ASCII')
                try:
                    request_mapping.insert(0, parse_mapping(header))
                except Invalid as e:
                    hint = ' (is the HOSTEXEC_WORKSPACE secret set?)' if header.endswith('=') else ''
                    return self.text(400, f'hostexec: {MAP_HEADER}: {e}{hint}')
            try:
                request = parse_exec_request(body, request_mapping)
            except Invalid as e:
                return self.text(400, f'hostexec: {e}')
            self.close_connection = True
            self.send_response(200)
            self.send_header('Content-Type', 'application/x-ndjson')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Transfer-Encoding', 'chunked')
            self.send_header('Connection', 'close')
            self.end_headers()
            Stream(self.connection, self.wfile, approvals, request, secrets, keepalive).run()

    return ApiHandler


class Stream:
    """One run's events, written as they come. This thread alone uses the
    connection: other threads (the approver, the command's output) only queue
    events here and wake it.

    {"event":"held","id":ID}                     once the request waits for a person
    {"event":"keepalive"}                        when nothing else was sent for a while
    {"event":"refused","reason":"denied"|"expired"}
    {"event":"started","cwd":DIR}
    {"event":"stdout"|"stderr","data":BASE64}
    {"event":"exit","exitCode":N|null,"signal":NAME|null}
    """

    def __init__(self, connection, wfile, approvals, request, secrets, keepalive):
        self._connection = connection
        self._wfile = wfile
        self._approvals = approvals
        self._request = request
        self._secrets = secrets
        self._keepalive = keepalive
        self._events = queue.Queue()
        self._wake_read, self._wake_write = os.pipe()
        self._gone = False

    def _put(self, kind, *args):
        self._events.put((kind, args))
        try:
            os.write(self._wake_write, b'.')
        except OSError:
            pass

    def _send(self, event):
        if self._gone:
            return
        data = json.dumps(event, separators=(',', ':')).encode() + b'\n'
        try:
            self._wfile.write(b'%x\r\n%s\r\n' % (len(data), data))
            self._wfile.flush()
        except (OSError, ValueError):
            # Gone as surely as when the read side ends: withdraw or stop.
            self._gone = True
            self._put('gone')
        self._last_sent = time.monotonic()

    def _client_left(self):
        """Whether the client has closed the connection. It has nothing more
        to send, so the connection reads only at its end."""
        try:
            self._connection.setblocking(False)
            try:
                return self._connection.recv(4096) == b''
            finally:
                self._connection.settimeout(IDLE_SECONDS)
        except (ssl.SSLWantReadError, ssl.SSLWantWriteError, BlockingIOError):
            return False
        except OSError:
            return True

    def _next(self):
        """The next queued event, sending keepalives and watching for the
        client to leave meanwhile."""
        while True:
            try:
                return self._events.get_nowait()
            except queue.Empty:
                pass
            if self._gone:
                watch, wait = [self._wake_read], None
            else:
                watch = [self._wake_read, self._connection]
                wait = max(0, self._last_sent + self._keepalive - time.monotonic())
            readable, _, _ = select.select(watch, [], [], wait)
            if self._wake_read in readable:
                os.read(self._wake_read, 4096)
            if self._connection in readable and not self._gone and self._client_left():
                self._gone = True
                return ('gone', ())
            if not readable and not self._gone:
                self._send({'event': 'keepalive'})

    def run(self):
        try:
            self._run()
        finally:
            os.close(self._wake_read)
            os.close(self._wake_write)

    def _run(self):
        self._last_sent = time.monotonic()
        held, withdraw = self._approvals.hold(self._request, lambda d: self._put('decision', d))
        self._send({'event': 'held', 'id': held.id})
        while True:
            kind, args = self._next()
            if kind == 'gone':
                withdraw()
            elif kind == 'decision':
                break
        decision, = args
        if decision != decisions.APPROVED:
            if decision != decisions.GONE:
                self._send({'event': 'refused', 'reason': decision})
            return self._end()
        if self._gone:
            return
        self._send({'event': 'started', 'cwd': self._request.cwd})
        run = Run(self._request, self._secrets,
                  lambda stream, data: self._put('output', stream, data),
                  lambda code, sig: self._put('exit', code, sig))
        while True:
            kind, args = self._next()
            if kind == 'gone':
                run.stop()
            elif kind == 'output':
                stream, data = args
                self._send({'event': stream, 'data': base64.b64encode(data).decode()})
            elif kind == 'exit':
                code, sig = args
                self._send({'event': 'exit', 'exitCode': code, 'signal': sig})
                return self._end()

    def _end(self):
        if self._gone:
            return
        try:
            self._wfile.write(b'0\r\n\r\n')
            self._wfile.flush()
        except (OSError, ValueError):
            pass
