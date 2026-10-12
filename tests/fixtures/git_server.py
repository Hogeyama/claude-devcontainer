import http.server
import os
from pathlib import Path
import ssl
import subprocess
from urllib.parse import urlsplit


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def handle_git(self):
        if self.headers.get('Transfer-Encoding'):
            self.send_error(411, 'Fixture expects Content-Length')
            return
        size = int(self.headers.get('Content-Length', '0'))
        body = self.rfile.read(size)
        url = urlsplit(self.path)
        environment = dict(os.environ, GIT_PROJECT_ROOT='/repos', GIT_HTTP_EXPORT_ALL='1',
                           PATH_INFO=url.path, QUERY_STRING=url.query,
                           REQUEST_METHOD=self.command,
                           CONTENT_TYPE=self.headers.get('Content-Type', ''),
                           CONTENT_LENGTH=str(size), REMOTE_USER='fixture',
                           HTTP_GIT_PROTOCOL=self.headers.get('Git-Protocol', ''))
        backend = subprocess.check_output(['git', '--exec-path'], text=True).strip()
        result = subprocess.run([str(Path(backend) / 'git-http-backend')],
                                env=environment, input=body, capture_output=True, check=True)
        headers, payload = result.stdout.split(b'\r\n\r\n', 1)
        pairs = [line.decode().split(':', 1) for line in headers.split(b'\r\n')]
        status = next((int(v.strip().split()[0]) for k, v in pairs if k.lower() == 'status'), 200)
        self.send_response(status)
        for key, value in pairs:
            if key.lower() not in ('status', 'content-length', 'connection'):
                self.send_header(key, value.strip())
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = handle_git


server = http.server.ThreadingHTTPServer(('0.0.0.0', 9443), Handler)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain('/fixtures/server.pem', '/fixtures/server.key')
server.socket = context.wrap_socket(server.socket, server_side=True)
server.serve_forever()
