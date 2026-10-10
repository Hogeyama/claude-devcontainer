"""Synthetic HTTPS capture server and production mitmdump launcher; no live services."""
import http.server
import json
import os
import signal
import ssl
import subprocess
import sys
import threading
import time
sys.path.insert(0, '/opt/proxy')
from entrypoint import COMMAND, publish_ca

class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def handle_request(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        with open('/tmp/upstream.jsonl', 'a') as output:
            output.write(json.dumps({'path': self.path, 'headers': list(self.headers.items()),
                                     'body': body.decode(), 'peer': self.client_address}) + '\n')
        data = b'{"fixture":"upstream-ok"}'
        if self.path.endswith('/git-upload-pack'):
            data = b'g' * (9 * 1024 * 1024)
        if b'"model": "sse-fixture"' in body:
            data = b'data: first\n\ndata: final\n\n'
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(b'data: first\n\n'); self.wfile.flush()
            time.sleep(1)
            self.wfile.write(b'data: final\n\n')
            return
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = handle_request

server = http.server.ThreadingHTTPServer(('127.0.0.1', 9443), Handler)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain('/fixtures/server.pem', '/fixtures/server.key')
server.socket = context.wrap_socket(server.socket, server_side=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
publish_ca()
args = list(COMMAND)
args[args.index('/opt/proxy/addon.py')] = '/fixtures/proxy_addon.py'
args.extend(['--set', 'ssl_verify_upstream_trusted_ca=' + os.environ.get('FIXTURE_TRUST', '/fixtures/server.pem')])
process = subprocess.Popen(args)
# Docker sends SIGTERM to this fixture's PID1, not its mitmdump child.
# Forward it and reap the child so restart exercises a clean proxy shutdown.
signal.signal(signal.SIGTERM, lambda signum, frame: process.send_signal(signum))
try:
    raise SystemExit(process.wait())
finally:
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=5)
    server.shutdown()
    server.server_close()
