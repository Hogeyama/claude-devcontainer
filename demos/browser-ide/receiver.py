"""Harmless stand-in for an external/enterprise endpoint. No real secrets."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json

LOG = Path('/tmp/received.jsonl')


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.record()

    def do_POST(self):
        self.record()

    def record(self):
        body = self.rfile.read(min(int(self.headers.get('Content-Length', 0)), 65536)).decode(errors='replace')
        event = {'method': self.command, 'path': self.path, 'body': body,
                 'origin': self.headers.get('Origin')}
        with LOG.open('a') as f:
            f.write(json.dumps(event) + '\n')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(b'<h1>Demo receiver received the request</h1>')


ThreadingHTTPServer(('0.0.0.0', 9000), Handler).serve_forever()
