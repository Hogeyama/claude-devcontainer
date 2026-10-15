"""Loopback-only app with root-relative assets, POST, redirects, WebSocket, and pages that refuse framing."""
import base64
import hashlib
import http.server


class Preview(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/socket':
            key = self.headers['Sec-WebSocket-Key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
            self.send_response(101)
            self.send_header('Connection', 'Upgrade')
            self.send_header('Upgrade', 'websocket')
            self.send_header('Sec-WebSocket-Accept', base64.b64encode(hashlib.sha1(key.encode()).digest()).decode())
            self.end_headers()
            self.wfile.write(b'\x81\x05hello')
            self.wfile.flush()
            return
        if self.path in ('/redirect', '/redirect-out'):
            self.send_response(302)
            self.send_header('Location', '/assets/app.js' if self.path == '/redirect' else 'http://example.com/')
            self.end_headers()
            return
        body = b'<script src="/assets/app.js"></script>' if self.path == '/' else b'preview-asset'
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Frame-Options', 'SAMEORIGIN')
        self.send_header('Content-Security-Policy', "default-src 'self'; frame-ancestors 'self'")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


http.server.ThreadingHTTPServer(('127.0.0.1', 3000), Preview).serve_forever()
