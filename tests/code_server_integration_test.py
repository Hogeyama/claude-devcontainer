"""The editor's server, code-server-proxy and the agent's firewall, with disposable Docker resources.

Build the two images first; RUN_DOCKER_TESTS=1 enables this test:
  docker build -f .devcontainer/claude/agent/Dockerfile --build-arg INSTALL_CODE_SERVER=1 \\
    --build-context workspace-config=.devcontainer -t claude-code-server-feature:test .devcontainer/claude
  docker build -t claude-code-server-proxy:test .devcontainer/claude/code-server
"""
import http.client
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import unittest
import uuid

PRE = 'static/out/vs/workbench/contrib/webview/browser/pre'


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()


@unittest.skipUnless(os.environ.get('RUN_DOCKER_TESTS') == '1', 'Docker integration disabled')
class CodeServerIntegrationTest(unittest.TestCase):
    def test_editor_and_browser_boundary(self):
        tag = 'cdc-code-server-' + uuid.uuid4().hex[:10]
        agent, proxy = tag + '-agent', tag + '-proxy'
        docker('network', 'create', '--internal', tag)
        self.addCleanup(docker, 'network', 'rm', tag)
        subnet = json.loads(docker('network', 'inspect', tag))[0]['IPAM']['Config'][0]['Subnet']
        network = ipaddress.ip_network(subnet)
        agent_ip, proxy_ip = str(network[20]), str(network[30])
        image = os.environ.get('CODE_SERVER_TEST_IMAGE', 'claude-code-server-feature:test')
        # Use the actual firewall, privilege drop and node entrypoint. The test
        # needs no host credentials, volumes, Docker socket or workspace data.
        docker('run', '-d', '--name', agent, '--network', tag, '--ip', agent_ip,
               '--cap-drop=ALL', '--cap-add=NET_ADMIN', '--cap-add=SETUID', '--cap-add=SETGID', '--cap-add=SETPCAP',
               '--security-opt=no-new-privileges', '--tmpfs', '/run/claude-devcontainer:uid=1000,gid=1000',
               '--sysctl', 'net.ipv4.conf.all.route_localnet=1',
               '-e', 'CLAUDE_DEVCONTAINER_CODE_SERVER=1',
               '-e', 'CLAUDE_DEVCONTAINER_CODE_SERVER_PROXY_ADDRESS=' + proxy_ip,
               '-e', 'CLAUDE_DEVCONTAINER_PROXY_ADDRESS=' + str(network[10]),
               '-e', 'CLAUDE_DEVCONTAINER_DIND_ADDRESS=' + str(network[11]),
               '--entrypoint', 'sh', image, '-ec',
               '/usr/local/bin/init-firewall.sh; exec setpriv --reuid=node --regid=node --init-groups '
               '--bounding-set=-all --inh-caps=-all --ambient-caps=-all --no-new-privs '
               '/usr/local/bin/entrypoint.sh --as-node sleep infinity')
        self.addCleanup(docker, 'rm', '-f', agent)
        docker('exec', agent, 'sh', '-ec', 'test ! -e /usr/local/bin/tode; test ! -e /opt/vscode; '
               'test -x /opt/code-server/bin/code-server')
        commit = json.loads(docker('exec', agent, 'cat', '/opt/code-server/lib/vscode/product.json'))['commit']
        # LANG is C.UTF-8, which the editor's terminals keep, and the image has that
        # locale: for one it lacks, locale writes a warning to stderr.
        self.assertEqual(docker('exec', agent, 'sh', '-c', 'echo $LANG; locale 2>&1 >/dev/null'), 'C.UTF-8')
        # code-server starts from the image; with no egress, a download would fail here.
        for _ in range(120):
            ready = subprocess.run(['docker', 'exec', agent, 'curl', '-fsS', '--noproxy', '*',
                                    'http://127.0.0.1:18000/healthz'], capture_output=True, text=True)
            if ready.returncode == 0:
                break
            time.sleep(0.5)
        else:
            self.fail(docker('exec', agent, 'cat', '/run/claude-devcontainer/code-server.log'))
        self.assertIn(json.loads(ready.stdout)['status'], ('alive', 'expired'))
        token = docker('exec', '-u', 'node', agent, 'cat', '/run/claude-devcontainer/vscode-token')
        machine = json.loads(docker('exec', '-u', 'node', agent, 'cat', '/home/node/.vscode-server/data/Machine/settings.json'))
        self.assertEqual(machine['workbench.externalUriOpeners'], {'http://localhost:*': 'simpleBrowser.open'})
        self.assertRegex(token, r'^[0-9a-f]{64}$')
        docker('run', '-d', '--name', proxy, '--network', tag, '--ip', proxy_ip,
               '--add-host', 'agent-code-server:' + agent_ip,
               '-p', '127.0.0.1::8080', '--read-only', '--tmpfs', '/home/mitmproxy/.mitmproxy:uid=1000,gid=1000,mode=0700',
               '--cap-drop=ALL', '--security-opt=no-new-privileges',
               os.environ.get('CODE_SERVER_PROXY_TEST_IMAGE', 'claude-code-server-proxy:test'))
        self.addCleanup(docker, 'rm', '-f', proxy)
        # A separate ingress interface makes the published localhost port
        # reachable, as in the shipped compose service.
        docker('network', 'connect', 'bridge', proxy)
        port = int(docker('port', proxy, '8080/tcp').split(':')[-1])

        def request(method, url, headers=None, body=None):
            conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
            conn.request(method, url, body=body, headers=headers or {})
            result = conn.getresponse()
            data = result.read()
            conn.close()
            return result.status, data, result.headers

        def upgrade(lines, expect):
            """A WebSocket upgrade through a CONNECT tunnel; the first response line, and what follows it."""
            authority = lines[0]
            with socket.create_connection(('127.0.0.1', port), 10) as sock:
                sock.settimeout(10)
                sock.sendall(f'CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n'.encode())
                self.assertIn(b'200', sock.recv(4096).split(b'\r\n')[0])
                sock.sendall('\r\n'.join(lines[1:] + ['Connection: Upgrade', 'Upgrade: websocket',
                                                       'Sec-WebSocket-Version: 13',
                                                       'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==', '', '']).encode())
                data = sock.recv(4096)
                self.assertIn(b'101', data.split(b'\r\n')[0])
                while expect not in data:
                    chunk = sock.recv(4096)
                    self.assertTrue(chunk, data)
                    data += chunk

        for _ in range(60):
            try:
                status, body, _ = request('GET', 'http://localhost:18000/healthz')
                break
            except (OSError, http.client.HTTPException):
                time.sleep(0.25)
        else:
            self.fail(docker('logs', proxy))
        self.assertEqual(status, 200)
        self.assertIn(json.loads(body)['status'], ('alive', 'expired'))
        cookie = {'Cookie': f'code-server-session={token}'}
        self.assertEqual(request('GET', 'http://localhost:18000/?folder=/workspace')[0], 302)
        self.assertEqual(request('GET', 'http://localhost:18000/?folder=/workspace',
                                 {'Cookie': 'code-server-session=wrong'})[0], 302)
        status, body, headers = request('GET', 'http://localhost:18000/?folder=/workspace', cookie)
        self.assertEqual(status, 200, body)
        self.assertIn(b'workbench', body)
        self.assertIn(b'&quot;enableWorkspaceTrust&quot;:false', body)
        self.assertIn(f'stable-{commit}/{PRE}'.encode(), body)
        self.assertNotIn(b'localhost:18001', body)
        for resource in ('index.html', 'service-worker.js'):
            status, body, _ = request('GET', f'http://localhost:18000/stable-{commit}/{PRE}/{resource}', cookie)
            self.assertEqual(status, 200, resource)
        upgrade(['localhost:18000',
                 f'GET /stable-{commit}?reconnectionToken=test&reconnection=false&skipWebSocketFrames=false HTTP/1.1',
                 'Host: localhost:18000', 'Origin: http://localhost:18000', f'Cookie: code-server-session={token}'], b'')
        for prefix in ('proxy', 'absproxy'):
            for suffix, app_path in [
                ('', '/'), ('?x=1', '/?x=1'),
                ('/foo?x=1', '/foo?x=1'), ('/foo/?x=1', '/foo/?x=1'),
                ('/login?next=%2F', '/login?next=%2F'),
            ]:
                url = f'http://localhost:18000/{prefix}/3000{suffix}'
                status, _, headers = request('GET', url, cookie)
                self.assertEqual((status, headers['Location']), (302, f'http://localhost:3000{app_path}'), url)

        # The agent's apps, on its loopback or every address, by the same port.
        fixture = Path(__file__).parent / 'fixtures/code_server_preview.py'
        docker('exec', '-d', '-u', 'node', agent, 'python3', '-c', fixture.read_text())
        docker('exec', '-d', '-u', 'node', agent, 'python3', '-c',
               'import http.server\n'
               'class A(http.server.BaseHTTPRequestHandler):\n'
               '    def do_GET(self):\n'
               '        self.send_response(200); self.end_headers(); self.wfile.write(b"any-" + self.headers["host"].encode())\n'
               'http.server.HTTPServer(("0.0.0.0", 3001), A).serve_forever()')
        for _ in range(40):
            status, body, headers = request('GET', 'http://localhost:3000/')
            if status == 200:
                break
            time.sleep(.1)
        self.assertEqual(status, 200, body)
        self.assertIn(b'src="/assets/app.js"', body)
        # Framed in the IDE's Simple Browser: the app's refusals are dropped.
        self.assertIsNone(headers['X-Frame-Options'])
        self.assertEqual(headers.get_all('Content-Security-Policy'), ["default-src 'self'"])
        self.assertEqual(request('GET', 'http://localhost:3000/assets/app.js')[:2], (200, b'preview-asset'))
        self.assertEqual(request('GET', 'http://localhost:3000/assets/app.js',
                                 headers={'Host': 'example.com', 'Forwarded': 'host=example.com',
                                          'X-Forwarded-Host': 'example.com'})[:2], (200, b'preview-asset'))
        self.assertEqual(request('POST', 'http://localhost:3000/submit', body=b'preview-post')[:2], (200, b'preview-post'))
        status, _, headers = request('GET', 'http://localhost:3000/redirect')
        self.assertEqual((status, headers['Location']), (302, '/assets/app.js'))
        self.assertEqual(request('GET', 'http://localhost:3000/redirect-out')[0], 451)
        for _ in range(40):
            status, body, _ = request('GET', 'http://localhost:3001/')
            if status == 200:
                break
            time.sleep(.1)
        self.assertEqual((status, body), (200, b'any-localhost:3001'))
        upgrade(['localhost:3000', 'GET /socket HTTP/1.1', 'Host: localhost:3000', 'Origin: http://localhost:3000'],
                b'hello')
        for method, url in [('GET', 'http://example.com/'), ('POST', 'http://example.com/'),
                            ('GET', 'http://127.0.0.1:18000/healthz'), ('GET', f'http://{agent_ip}:3000/'),
                            ('GET', 'http://ide.localhost:8000/'), ('GET', 'http://port-3000.localhost:8000/'),
                            ('GET', 'http://localhost.:3000/'), ('CONNECT', 'example.com:443'),
                            ('CONNECT', '127.0.0.1:3000'), ('CONNECT', 'app.localhost:3000')]:
            self.assertEqual(request(method, url)[0], 451, (method, url))
        # A peer on the same Docker network is not the browser proxy: its
        # connections are not forwarded to loopback, and INPUT drops them.
        for target in (18000, 3000, 3001):
            blocked = docker('run', '--rm', '--network', tag, '--entrypoint', 'python3', image, '-c',
                             'import socket\ns=socket.socket();s.settimeout(1)\n'
                             f'try: s.connect(({agent_ip!r},{target}));print("connected")\n'
                             'except OSError: print("blocked")')
            self.assertEqual(blocked, 'blocked', target)
        disabled = docker('run', '--rm', '--network', 'none', '--user', '1000:1000',
                          '-e', 'CLAUDE_DEVCONTAINER_CODE_SERVER=0', image,
                          '--as-node', 'sh', '-ec', 'sleep 1; ! pgrep -f "[/]opt/code-server"')
        self.assertEqual(disabled, '')


if __name__ == '__main__':
    unittest.main()
