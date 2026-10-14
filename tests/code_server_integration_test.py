"""Actual editor/proxy and agent firewall, with disposable Docker resources.

Build claude-code-server-feature:test (INSTALL_CODE_SERVER=1, INSTALL_TODE=0)
and claude-code-server-proxy:test first. RUN_DOCKER_TESTS=1 enables this test.
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
               '-e', 'CLAUDE_DEVCONTAINER_CODE_SERVER=1',
               '-e', 'CLAUDE_DEVCONTAINER_CODE_SERVER_PROXY_ADDRESS=' + proxy_ip,
               '-e', 'CLAUDE_DEVCONTAINER_PROXY_ADDRESS=' + str(network[10]),
               '-e', 'CLAUDE_DEVCONTAINER_DIND_ADDRESS=' + str(network[11]),
               '--entrypoint', 'sh', image, '-ec',
               '/usr/local/bin/init-firewall.sh; exec setpriv --reuid=node --regid=node --init-groups '
               '--bounding-set=-all --inh-caps=-all --ambient-caps=-all --no-new-privs '
               '/usr/local/bin/entrypoint.sh --as-node sleep infinity')
        self.addCleanup(docker, 'rm', '-f', agent)
        docker('exec', agent, 'sh', '-ec', 'test ! -e /usr/local/bin/tode; test -x /opt/code-server/bin/code-server')
        for _ in range(60):
            ready = subprocess.run(['docker', 'exec', agent, 'curl', '-fsS', '--noproxy', '*',
                                    'http://127.0.0.1:8088/healthz'], capture_output=True)
            if ready.returncode == 0:
                break
            time.sleep(0.25)
        else:
            self.fail(docker('exec', agent, 'cat', '/run/claude-devcontainer/code-server.log'))
        docker('run', '-d', '--name', proxy, '--network', tag, '--ip', proxy_ip,
               '--add-host', 'agent-code-server:' + agent_ip,
               '-p', '127.0.0.1::8080', '--read-only', '--tmpfs', '/home/mitmproxy/.mitmproxy:uid=1000,gid=1000,mode=0700',
               '--cap-drop=ALL', '--security-opt=no-new-privileges',
               'claude-code-server-proxy:test')
        self.addCleanup(docker, 'rm', '-f', proxy)
        # A separate ingress interface makes the published localhost port
        # reachable, as in the shipped compose service.
        docker('network', 'connect', 'bridge', proxy)
        port = int(docker('port', proxy, '8080/tcp').split(':')[-1])
        for _ in range(60):
            try:
                with socket.create_connection(('127.0.0.1', port), 0.5):
                    break
            except OSError:
                time.sleep(0.25)
        def request(method, url, headers=None, body=None):
            conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
            conn.request(method, url, body=body, headers=headers or {})
            result = conn.getresponse()
            body = result.read()
            conn.close()
            return result.status, body
        for attempt in range(60):
            try:
                status, body = request('GET', 'http://ide.localhost:8000/healthz')
                break
            except (OSError, http.client.HTTPException):
                time.sleep(0.25)
        else:
            self.fail(docker('logs', proxy))
        self.assertEqual(status, 200, body)
        self.assertIn('status', json.loads(body))
        status, body = request('GET', 'http://ide.localhost:8000/?folder=/workspace')
        self.assertEqual(status, 200, body)
        self.assertIn(b'workbench', body)
        for method, url in [('POST', 'http://example.com/'), ('GET', 'http://127.0.0.1:8088/'),
                            ('CONNECT', 'example.com:443'), ('CONNECT', 'ide.localhost:8001')]:
            self.assertEqual(request(method, url)[0], 451, (method, url))
        # Exercise the same CONNECT + WebSocket upgrade used by the browser.
        with socket.create_connection(('127.0.0.1', port), 5) as sock:
            sock.settimeout(5)
            sock.sendall(b'CONNECT ide.localhost:8000 HTTP/1.1\r\nHost: ide.localhost:8000\r\n\r\n')
            self.assertIn(b'200', sock.recv(4096).split(b'\r\n')[0])
            sock.sendall(b'GET /websocket?reconnection=false&reconnectionToken=test HTTP/1.1\r\n'
                         b'Host: ide.localhost:8000\r\nOrigin: http://ide.localhost:8000\r\n'
                         b'Connection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Version: 13\r\n'
                         b'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n')
            self.assertIn(b'101', sock.recv(4096).split(b'\r\n')[0])
        fixture = Path(__file__).parent / 'fixtures/code_server_preview.py'
        docker('exec', '-d', '-u', 'node', agent, 'python3', '-c', fixture.read_text())
        for _ in range(40):
            status, body = request('GET', 'http://port-3000.localhost:8000/')
            if status == 200:
                break
            time.sleep(.1)
        self.assertEqual(status, 200, body)
        self.assertIn(b'src="/assets/app.js"', body)
        self.assertEqual(request('GET', 'http://port-3000.localhost:8000/assets/app.js'), (200, b'preview-asset'))
        self.assertEqual(request('GET', 'http://port-3000.localhost:8000/assets/app.js',
                                 headers={'Host': 'example.com', 'Forwarded': 'host=example.com',
                                          'X-Forwarded-Host': 'example.com'}), (200, b'preview-asset'))
        self.assertEqual(request('POST', 'http://port-3000.localhost:8000/submit', body=b'preview-post'),
                         (200, b'preview-post'))
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        conn.request('GET', 'http://port-3000.localhost:8000/redirect')
        response = conn.getresponse()
        self.assertEqual(response.status, 302)
        self.assertEqual(response.getheader('Location'), '/assets/app.js')
        response.read()
        conn.close()
        for host in ('port-0.localhost', 'port-65536.localhost', 'port-8088.localhost',
                     'port-03000.localhost', 'port-3000.localhost.evil.test',
                     'port-3000-127.0.0.1.localhost', 'other.localhost'):
            for method, url in [('GET', f'http://{host}:8000/'), ('CONNECT', f'{host}:8000')]:
                self.assertEqual(request(method, url)[0], 451, (method, url))
        with socket.create_connection(('127.0.0.1', port), 5) as sock:
            sock.settimeout(5)
            sock.sendall(b'CONNECT port-3000.localhost:8000 HTTP/1.1\r\nHost: port-3000.localhost:8000\r\n\r\n')
            self.assertIn(b'200', sock.recv(4096).split(b'\r\n')[0])
            sock.sendall(b'GET /socket HTTP/1.1\r\nHost: port-3000.localhost:8000\r\n'
                         b'Origin: http://port-3000.localhost:8000\r\nConnection: Upgrade\r\n'
                         b'Upgrade: websocket\r\nSec-WebSocket-Version: 13\r\n'
                         b'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n')
            data = sock.recv(4096)
            self.assertIn(b'101', data.split(b'\r\n')[0])
            while b'hello' not in data:
                chunk = sock.recv(4096)
                self.assertTrue(chunk, data)
                data += chunk
        # The ports panel must generate the same host-based URLs.
        env = docker('exec', '-u', 'node', agent, 'sh', '-c', 'cat /proc/$(pgrep -fo "[/]opt/code-server/lib/node /opt/code-server --")/environ')
        self.assertIn('VSCODE_PROXY_URI=http://port-{{port}}.localhost:8000', env)
        # A peer on the same Docker network is not the permitted browser proxy.
        blocked = docker('run', '--rm', '--network', tag, '--entrypoint', 'python3', image, '-c',
                         'import socket\ns=socket.socket();s.settimeout(1)\n'
                         f'try: s.connect(({agent_ip!r},8088));print("connected")\n'
                         'except OSError: print("blocked")')
        self.assertEqual(blocked, 'blocked')
        disabled = docker('run', '--rm', '--network', 'none', '--user', '1000:1000',
                          '-e', 'CLAUDE_DEVCONTAINER_CODE_SERVER=0', image,
                          '--as-node', 'sh', '-ec', 'sleep 1; ! pgrep -f "[/]opt/code-server"')
        self.assertEqual(disabled, '')


if __name__ == '__main__':
    unittest.main()
