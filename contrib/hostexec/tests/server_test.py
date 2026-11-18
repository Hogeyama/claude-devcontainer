"""The two servers over HTTP, and `hostexec serve` as a whole over TLS."""
import base64
import http.client
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hostexec_host.api import Server, Stream, api_handler  # noqa: E402
from hostexec_host.approvals import Approvals  # noqa: E402
from hostexec_host.request import ExecRequest  # noqa: E402
from hostexec_host.ui import ui_handler  # noqa: E402

TOKEN = 'a' * 64
UI_TOKEN = 'b' * 64


def events(body):
    return [json.loads(line) for line in body.decode().splitlines() if line]


def output(evs, stream='stdout'):
    return b''.join(base64.b64decode(e['data']) for e in evs if e['event'] == stream)


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.approvals = Approvals(2)
        cls.api = Server(('127.0.0.1', 0), api_handler(
            TOKEN, cls.approvals, [('/workspace', '/')], [TOKEN], '#!client', keepalive=0.05))
        cls.ui = Server(('127.0.0.1', 0), ui_handler(
            lambda: cls.authority, UI_TOKEN, cls.approvals,
            {'/': ('<html>', 'text/html'), '/app.js': ('js', 'text/javascript')}))
        cls.authority = f'127.0.0.1:{cls.ui.server_address[1]}'
        for server in (cls.api, cls.ui):
            threading.Thread(target=server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        for server in (cls.api, cls.ui):
            server.shutdown()
            server.server_close()

    def request(self, server, method, path, body=None, headers=None):
        c = http.client.HTTPConnection(*server.server_address, timeout=10)
        c.request(method, path, body=body, headers=headers or {})
        res = c.getresponse()
        data = res.read()
        c.close()
        return res.status, data

    def run_body(self, body, token=TOKEN, headers=None):
        return self.request(self.api, 'POST', '/run', json.dumps(body),
                            {'Authorization': f'Bearer {token}', **(headers or {})})

    def ui_call(self, path, body, headers=None):
        status, data = self.request(self.ui, 'POST', path, json.dumps(body), {
            'Authorization': f'Bearer {UI_TOKEN}', 'Origin': f'http://{self.authority}',
            'Host': self.authority, **(headers or {})})
        return status, json.loads(data)

    def in_background(self, fn, *args, **kwargs):
        result = {}
        t = threading.Thread(target=lambda: result.update(value=fn(*args, **kwargs)), daemon=True)
        t.start()
        return lambda: (t.join(10), result['value'])[1]

    def wait_held(self):
        for _ in range(250):
            _, data = self.ui_call('/api/pending', {})
            if data['pending']:
                return data['pending'][0]
            time.sleep(0.02)
        self.fail('nothing held')

    def wait_empty(self):
        for _ in range(250):
            if not self.approvals.list():
                return
            time.sleep(0.02)
        self.fail('still held')

    def test_needs_the_token(self):
        self.assertEqual(self.run_body({'argv': ['true'], 'cwd': '/'}, 'wrong')[0], 401)
        self.assertEqual(self.request(self.api, 'GET', '/client')[0], 401)

    def test_serves_the_client(self):
        self.assertEqual(self.request(self.api, 'GET', '/client', headers={'Authorization': f'Bearer {TOKEN}'}),
                         (200, b'#!client'))
        self.assertEqual(self.request(self.api, 'GET', '/client?x', headers={'Authorization': f'Bearer {TOKEN}'})[0],
                         400)

    def test_refuses_a_bad_request_before_holding_it(self):
        self.assertEqual(self.run_body({'argv': ['true'], 'cwd': '/', 'extra': 1})[0], 400)
        auth = {'Authorization': f'Bearer {TOKEN}'}
        self.assertEqual(self.request(self.api, 'GET', '/run', headers=auth)[0], 405)
        self.assertEqual(self.request(self.api, 'POST', '/other', '{}', auth)[0], 404)
        self.assertEqual(self.request(self.api, 'POST', '/run', '{}',
                                      {**auth, 'Content-Encoding': 'gzip'})[0], 400)
        self.assertEqual(self.approvals.list(), [])

    def test_approved_runs_in_the_mapped_directory_and_streams_the_output(self):
        pending = self.in_background(self.run_body, {'argv': ['sh', '-c', f'pwd; echo {TOKEN}; exit 4'],
                                                     'cwd': '/workspace/tmp'})
        held = self.wait_held()
        self.assertEqual(self.ui_call('/api/decision', {'id': held['id'], 'approve': True})[0], 200)
        status, body = pending()
        evs = [e for e in events(body) if e['event'] != 'keepalive']
        self.assertEqual(status, 200)
        self.assertEqual(evs[0], {'event': 'held', 'id': held['id']})
        self.assertIn({'event': 'started', 'cwd': '/tmp'}, evs)
        self.assertEqual(output(evs), b'/tmp\n[masked by hostexec]\n')
        self.assertEqual(evs[-1], {'event': 'exit', 'exitCode': 4, 'signal': None})

    def test_the_proxys_map_names_the_workspace_over_map(self):
        pending = self.in_background(self.run_body, {'argv': ['pwd'], 'cwd': '/workspace/bin'},
                                     headers={'X-Hostexec-Map': '/workspace=/usr'})
        held = self.wait_held()
        self.assertEqual((held['cwd'], held['requestedCwd']), ('/usr/bin', '/workspace/bin'))
        self.ui_call('/api/decision', {'id': held['id'], 'approve': True})
        self.assertIn({'event': 'started', 'cwd': '/usr/bin'}, events(pending()[1]))

    def test_a_bad_map_header_is_refused(self):
        for header, says in [('relative', b'X-Hostexec-Map'), ('/workspace=', b'HOSTEXEC_WORKSPACE'),
                             ('/workspace=/home/u/caf\xe9'.encode('utf-8').decode('latin-1'), b'ASCII')]:
            with self.subTest(header):
                status, body = self.run_body({'argv': ['pwd'], 'cwd': '/workspace'},
                                             headers={'X-Hostexec-Map': header})
                self.assertEqual(status, 400)
                self.assertIn(says, body)

    def test_denied(self):
        pending = self.in_background(self.run_body, {'argv': ['touch', '/tmp/should-not-exist'], 'cwd': '/'})
        held = self.wait_held()
        self.ui_call('/api/decision', {'id': held['id'], 'approve': False})
        evs = events(pending()[1])
        self.assertEqual(evs[-1], {'event': 'refused', 'reason': 'denied'})
        self.assertNotIn('started', [e['event'] for e in evs])

    def test_expired_with_keepalives_while_it_waits(self):
        evs = events(self.run_body({'argv': ['true'], 'cwd': '/'})[1])
        self.assertIn({'event': 'keepalive'}, evs)
        self.assertEqual(evs[-1], {'event': 'refused', 'reason': 'expired'})

    def raw_run(self, body):
        s = socket.create_connection(self.api.server_address)
        data = json.dumps(body).encode()
        s.sendall(b'POST /run HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer ' + TOKEN.encode() +
                  b'\r\nContent-Length: %d\r\n\r\n' % len(data) + data)
        return s

    def test_a_client_that_goes_away_withdraws_its_request(self):
        s = self.raw_run({'argv': ['true'], 'cwd': '/'})
        self.wait_held()
        s.close()
        self.wait_empty()

    def test_a_client_that_goes_away_stops_its_command(self):
        with tempfile.TemporaryDirectory() as d:
            marker = Path(d) / 'still-running'
            s = self.raw_run({'argv': ['sh', '-c', f'sleep 1; touch {marker}'], 'cwd': '/'})
            held = self.wait_held()
            self.ui_call('/api/decision', {'id': held['id'], 'approve': True})
            time.sleep(0.3)
            s.close()
            time.sleep(1.5)
            self.assertFalse(marker.exists())

    def test_a_failed_write_stops_the_command(self):
        class Broken:
            def write(self, data):
                if b'started' in data:
                    raise BrokenPipeError
            def flush(self):
                pass
        quiet, other = socket.socketpair()
        self.addCleanup(quiet.close)
        self.addCleanup(other.close)
        request = ExecRequest(argv=['sleep', '30'], cwd='/', requested_cwd='/', env={})
        stream = Stream(quiet, Broken(), self.approvals, request, [], 10)
        done = threading.Thread(target=stream.run, daemon=True)
        done.start()
        held = self.wait_held()
        self.ui_call('/api/decision', {'id': held['id'], 'approve': True})
        done.join(5)
        self.assertFalse(done.is_alive())

    def test_ui_serves_the_page_to_anyone_the_api_only_with_token_origin_and_host(self):
        self.assertEqual(self.request(self.ui, 'GET', '/', headers={'Host': self.authority}), (200, b'<html>'))
        self.assertEqual(self.request(self.ui, 'GET', '/?x', headers={'Host': self.authority})[0], 403)
        self.assertEqual(self.ui_call('/api/pending', {})[0], 200)
        self.assertEqual(self.ui_call('/api/pending', {}, {'Authorization': 'Bearer x'})[0], 403)
        self.assertEqual(self.ui_call('/api/pending', {}, {'Origin': 'http://evil.example'})[0], 403)
        self.assertEqual(self.ui_call('/api/pending', {}, {'Sec-Fetch-Site': 'cross-site'})[0], 403)
        port = self.ui.server_address[1]
        self.assertEqual(self.ui_call('/api/pending', {}, {'Host': f'localhost:{port}',
                                                          'Origin': f'http://localhost:{port}'})[0], 403)

    def test_ui_refuses_bad_bodies_and_unknown_ids(self):
        self.assertEqual(self.ui_call('/api/decision', {'id': 'nope', 'approve': True})[0], 404)
        self.assertEqual(self.ui_call('/api/decision', {'id': 'nope', 'approve': 1})[0], 400)
        self.assertEqual(self.ui_call('/api/pending', [])[0], 400)
        self.assertEqual(self.ui_call('/api/pending', 'x' * 5000)[0], 400)


class HostexecTest(unittest.TestCase):
    """`hostexec init`, `serve` and `review` as processes, with TLS."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.env = {**os.environ, 'XDG_CONFIG_HOME': directory.name}
        self.config = Path(directory.name) / 'hostexec'
        self.hostexec = [sys.executable, str(ROOT / 'hostexec')]

    def free_port(self):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            return s.getsockname()[1]

    def start(self, *args, **kwargs):
        p = subprocess.Popen([*self.hostexec, *args], env=self.env, stderr=subprocess.PIPE, text=True,
                             **kwargs)
        self.addCleanup(lambda: (p.kill(), p.wait(), p.stderr.close(), p.stdin and p.stdin.close()))
        return p

    def test_init_serve_and_review(self):
        subprocess.run([*self.hostexec, 'init'], env=self.env, check=True, capture_output=True)
        self.assertEqual(oct(self.config.stat().st_mode & 0o777), '0o700')
        self.assertEqual(sorted(p.name for p in self.config.iterdir()),
                         ['api-token', 'ca.pem', 'server-key.pem', 'server.pem', 'ui-token'])
        again = subprocess.run([*self.hostexec, 'init'], env=self.env, capture_output=True)
        self.assertEqual(again.returncode, 2)

        api_port, ui_port = self.free_port(), self.free_port()
        server = self.start('serve', '--listen', '127.0.0.1', '--port', str(api_port),
                            '--ui-port', str(ui_port), '--map', '/workspace=/', '--no-notify')
        self.assertIn('taking requests', server.stderr.readline())

        tls = ssl.create_default_context(cafile=self.config / 'ca.pem')
        c = http.client.HTTPConnection('127.0.0.1', api_port)
        c.sock = tls.wrap_socket(socket.create_connection(('127.0.0.1', api_port), timeout=10),
                                 server_hostname='host.docker.internal')
        token = (self.config / 'api-token').read_text().strip()
        body = json.dumps({'argv': ['sh', '-c', 'echo hi; exit 5'], 'cwd': '/workspace/tmp'})
        c.request('POST', '/run', body, {'Authorization': f'Bearer {token}'})

        ui = http.client.HTTPConnection('127.0.0.1', ui_port, timeout=10)
        ui.request('GET', '/favicon.svg')
        icon = ui.getresponse()
        self.assertEqual((icon.status, icon.getheader('Content-Type')), (200, 'image/svg+xml; charset=utf-8'))
        self.assertIn(b'<svg', icon.read())
        ui.close()

        # A proxy in the environment would get the UI token: review goes straight to loopback.
        self.env.update(http_proxy='http://127.0.0.1:9', HTTP_PROXY='http://127.0.0.1:9', no_proxy='')
        review = self.start('review', '--ui-port', str(ui_port), stdin=subprocess.PIPE)
        review.stdin.write('y\n')
        review.stdin.flush()
        evs = events(c.getresponse().read())
        self.assertEqual(output(evs), b'hi\n')
        self.assertIn({'event': 'started', 'cwd': '/tmp'}, evs)
        self.assertEqual(evs[-1], {'event': 'exit', 'exitCode': 5, 'signal': None})

        with socket.create_connection(('127.0.0.1', api_port)) as raw, \
                self.assertRaises(ssl.SSLCertVerificationError):
            tls.wrap_socket(raw, server_hostname='example.com')

        server.terminate()
        self.assertEqual(server.wait(10), 0)


if __name__ == '__main__':
    unittest.main()
