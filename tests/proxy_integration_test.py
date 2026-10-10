"""Real mitmproxy/TLS tests with synthetic credentials. RUN_DOCKER_TESTS=1 opts in."""
import base64
import http.client
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
PROXY_IMAGE = os.environ.get('PROXY_TEST_IMAGE', 'claude-devcontainer-proxy:task1')
TOKEN = 'synthetic-managed-token'


def docker(*args, **kwargs):
    return subprocess.run(['docker', *args], text=True, capture_output=True, timeout=120, check=True, **kwargs).stdout


def fixtures(directory):
    """Copy the proxy fixtures and create a throwaway upstream certificate in directory."""
    for name in ('proxy_addon.py', 'proxy_server.py'):
        (directory / name).write_bytes((ROOT / 'tests/fixtures' / name).read_bytes())
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                    '-keyout', str(directory / 'server.key'), '-out', str(directory / 'server.pem'),
                    '-subj', '/CN=api.github.com', '-addext',
                    'subjectAltName=DNS:api.github.com,DNS:github.com,DNS:api.anthropic.com'],
                   check=True, capture_output=True)
    for path in [directory, *directory.iterdir()]:
        path.chmod(0o755)  # Readable by the proxy's UID 1000.


@unittest.skipUnless(os.environ.get('RUN_DOCKER_TESTS') == '1', 'Docker integration disabled; set RUN_DOCKER_TESTS=1')
class ProxyIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix='cdc-proxy-')
        cls.addClassCleanup(temporary.cleanup)
        cls.directory = Path(temporary.name)
        fixtures(cls.directory)

    def start(self, fixture=True, trust='/fixtures/server.pem'):
        self.name = 'cdc-proxy-' + uuid.uuid4().hex[:12]
        self.addCleanup(docker, 'rm', '-fv', self.name)
        args = ['run', '-d', '--name', self.name, '-p', '127.0.0.1::8080',
                '--tmpfs', '/run/proxy-credentials:uid=1000,gid=1000,mode=0700,size=1m']
        if fixture:
            args += ['-v', f'{self.directory}:/fixtures:ro', '-e', 'FIXTURE_TRUST=' + trust,
                     '--entrypoint', 'python', PROXY_IMAGE, '/fixtures/proxy_server.py']
        else:
            args.append(PROXY_IMAGE)
        docker(*args)
        self.port = int(docker('port', self.name, '8080').strip().rsplit(':', 1)[1])
        for _ in range(100):
            try:
                self.context = ssl.create_default_context(cadata=docker('exec', self.name, 'cat', '/public-ca/mitmproxy-ca-cert.pem'))
                # The published port accepts connections before mitmdump listens.
                docker('exec', self.name, 'python', '-c', "import socket; socket.create_connection(('127.0.0.1', 8080), .2)")
                return
            except (OSError, subprocess.CalledProcessError):
                time.sleep(.1)
        self.fail('proxy not ready: ' + docker('logs', self.name))

    def inject(self, mode='api-key', github=TOKEN, anthropic=TOKEN):
        docker('exec', '-i', self.name, 'python', '/opt/proxy/credentials.py', 'inject',
               input=json.dumps(dict(github_token=github, anthropic_token=anthropic, auth_mode=mode)))

    def captures(self):
        output = docker('exec', self.name, 'sh', '-c', 'cat /tmp/upstream.jsonl 2>/dev/null || true')
        return [json.loads(line) for line in output.splitlines()]

    def connection(self, host='api.github.com'):
        connection = http.client.HTTPSConnection('127.0.0.1', self.port, context=self.context, timeout=5)
        connection.set_tunnel(host, 443)
        self.addCleanup(connection.close)
        return connection

    def request(self, host='api.github.com', path='/user', body=None, headers=None):
        connection = self.connection(host)
        connection.request('POST' if body is not None else 'GET', path, body, headers or {})
        response = connection.getresponse()
        return response.status, response.read()

    def messages(self, headers=None, **extra):
        body = json.dumps({'model': 'fixture', 'max_tokens': 1, 'messages': [], **extra})
        return self.request('api.anthropic.com', '/v1/messages', body, {'Content-Type': 'application/json', **(headers or {})})

    def test_production_startup_publishes_only_the_certificate_and_denies_without_credentials(self):
        self.start(fixture=False)
        self.assertNotIn('PRIVATE KEY', docker('exec', self.name, 'cat', '/public-ca/mitmproxy-ca-cert.pem'))
        self.assertEqual(self.request()[0], 403)

    def test_credentials_are_replaced_rotated_and_removed(self):
        self.start()
        self.assertEqual(self.request()[0], 403)
        self.inject()
        attacker = {'Authorization': 'Bearer attacker', 'Cookie': 'session=attacker', 'X-Api-Key': 'attacker'}
        self.assertEqual(self.request(headers=attacker)[0], 200)
        self.assertEqual(self.request('github.com', '/o/r.git/info/refs?service=git-upload-pack', headers=attacker)[0], 200)
        self.assertEqual(self.messages(attacker)[0], 200)
        self.inject('oauth', 'rotated-pat', 'rotated-oauth')
        self.assertEqual(self.messages(attacker)[0], 200)
        github, git, api_key, oauth = [{k.lower(): v for k, v in item['headers']} for item in self.captures()]
        self.assertEqual(github['authorization'], 'Bearer ' + TOKEN)
        self.assertEqual(git['authorization'], 'Basic ' + base64.b64encode(f'x-access-token:{TOKEN}'.encode()).decode())
        self.assertEqual((api_key['x-api-key'], 'authorization' in api_key), (TOKEN, False))
        self.assertEqual((oauth['authorization'], 'x-api-key' in oauth), ('Bearer rotated-oauth', False))
        self.assertIn('oauth-2025-04-20', oauth['anthropic-beta'])
        self.assertNotIn('cookie', github)
        rejected = subprocess.run(['docker', 'exec', '-i', self.name, 'python', '/opt/proxy/credentials.py', 'inject'],
                                  input='{"github_token": "x"}', text=True, capture_output=True)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(self.request()[0], 200)
        self.assertEqual(dict(self.captures()[-1]['headers'])['Authorization'], 'Bearer rotated-pat')
        docker('exec', self.name, 'rm', '/run/proxy-credentials/credentials.json')
        self.assertEqual(self.request()[0], 403)
        self.assertNotIn(TOKEN, docker('logs', self.name))

    def test_denied_requests_never_reach_upstream(self):
        self.start()
        self.inject()
        self.assertEqual(self.request(path='/gists')[0], 403)
        for extra in ({'mcp_servers': [{'type': 'url', 'url': 'https://attacker.invalid'}]},
                      {'tools': [{'type': 'web_fetch_20250910', 'name': 'web_fetch'}]},
                      {'messages': [{'role': 'user', 'content': [{'type': 'image', 'source': {'type': 'url', 'url': 'https://attacker.invalid'}}]}]}):
            self.assertEqual(self.messages(**extra)[0], 403)
        for headers in ({'Transfer-Encoding': 'chunked'}, {'Content-Length': str(8 * 1024 * 1024 + 1)}):
            connection = self.connection('api.anthropic.com')
            connection.putrequest('POST', '/v1/messages')
            for key, value in headers.items():
                connection.putheader(key, value)
            connection.endheaders()
            with self.assertRaises((http.client.RemoteDisconnected, ConnectionResetError)):
                connection.getresponse()
        for payload in (b'GET http://api.github.com/user HTTP/1.1\r\nHost: api.github.com\r\n\r\n',
                        b'CONNECT api.github.com:444 HTTP/1.1\r\nHost: api.github.com:444\r\n\r\n',
                        b'CONNECT attacker.invalid:443 HTTP/1.1\r\nHost: attacker.invalid:443\r\n\r\n',
                        b'\x05\x01\x00', b'raw-protocol\r\n\r\n'):
            with socket.create_connection(('127.0.0.1', self.port), 2) as sock:
                sock.settimeout(.5)
                sock.sendall(payload)
                try:
                    data = sock.recv(4096)
                except (socket.timeout, ConnectionResetError):
                    data = b''
                self.assertNotIn(b'200 Connection established', data)
        self.assertEqual(self.captures(), [])

    def test_large_git_response_and_server_sent_events_stream(self):
        self.start()
        self.inject()
        status, data = self.request('github.com', '/o/r.git/git-upload-pack', b'git-fixture')
        self.assertEqual((status, len(data)), (200, 9 * 1024 * 1024))
        connection = self.connection('api.anthropic.com')
        started = time.monotonic()
        connection.request('POST', '/v1/messages', json.dumps({'model': 'sse-fixture', 'max_tokens': 1, 'messages': [], 'stream': True}),
                           {'Content-Type': 'application/json'})
        response = connection.getresponse()
        self.assertEqual(response.read(len(b'data: first\n\n')), b'data: first\n\n')
        self.assertLess(time.monotonic() - started, .8, 'events must arrive before upstream finishes')
        self.assertEqual(response.read(), b'data: final\n\n')

    def test_untrusted_upstream_certificate_is_rejected(self):
        self.start(trust='/etc/ssl/certs/ca-certificates.crt')
        self.inject()
        self.assertEqual(self.request()[0], 502)
        self.assertEqual(self.captures(), [])


if __name__ == '__main__':
    unittest.main()
