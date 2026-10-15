"""The allowlist of code-server-proxy (code-server/proxy.py), with a stand-in for mitmproxy's API.

tests/code_server_integration_test.py runs it in the real proxy image.
"""
import importlib.util
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Headers:
    """mitmproxy's Headers, as far as proxy.py uses them: case-insensitive, with repeated fields."""
    def __init__(self, fields=()):
        self.fields = list(fields)

    def get(self, name, default=None):
        values = self.get_all(name)
        return ', '.join(values) if values else default

    def get_all(self, name):
        return [value for key, value in self.fields if key.lower() == name.lower()]

    def set_all(self, name, values):
        self.fields = [(key, value) for key, value in self.fields if key.lower() != name.lower()]
        self.fields += [(name, value) for value in values]

    def pop(self, name, default=None):
        value = self.get(name, default)
        self.set_all(name, [])
        return value

    def __setitem__(self, name, value):
        self.set_all(name, [value])


class Response:
    def __init__(self, status_code, headers=()):
        self.status_code = status_code
        self.headers = Headers(headers)

    @classmethod
    def make(cls, status_code, content, headers):
        return cls(status_code, headers.items())


def load_proxy():
    log = types.SimpleNamespace(warn=lambda message: None, info=lambda message: None)
    mitmproxy = types.ModuleType('mitmproxy')
    mitmproxy.http = types.SimpleNamespace(Response=Response)
    mitmproxy.ctx = types.SimpleNamespace(log=log)
    sys.modules['mitmproxy'] = mitmproxy
    try:
        spec = importlib.util.spec_from_file_location('code_server_proxy', ROOT / '.devcontainer/claude/code-server/proxy.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        del sys.modules['mitmproxy']


proxy = load_proxy()


def flow(host, port, scheme='http', headers=()):
    request = types.SimpleNamespace(host=host, port=port, scheme=scheme, method='GET', path='/',
                                    pretty_url=f'{scheme}://{host}:{port}/', headers=Headers(headers))
    return types.SimpleNamespace(request=request, response=None, metadata={},
                                 server_conn=types.SimpleNamespace(address=None))


class ProxyTest(unittest.TestCase):
    def test_ports_map_to_the_agent(self):
        self.assertEqual(proxy.target_for('localhost', 18000), ('agent-code-server', 18000))
        self.assertEqual(proxy.target_for('localhost', 18001), ('agent-code-server', 18001))
        self.assertEqual(proxy.target_for('localhost', 3000), ('agent-code-server', 3000))
        for host, port in [('example.com', 80), ('127.0.0.1', 3000), ('ide.localhost', 18000),
                           ('localhost.', 3000), ('agent-code-server', 18000), ('localhost', 0), ('localhost', 65536)]:
            self.assertIsNone(proxy.target_for(host, port), (host, port))

    def test_request_goes_to_the_agent_with_the_shells_host(self):
        allowed = flow('localhost', 3000, headers=[('Host', 'example.com'), ('Forwarded', 'host=x'),
                                                    ('X-Forwarded-Host', 'x')])
        proxy.request(allowed)
        self.assertIsNone(allowed.response)
        self.assertEqual((allowed.request.host, allowed.request.port), ('agent-code-server', 3000))
        self.assertEqual(allowed.request.headers.get_all('host'), ['localhost:3000'])
        self.assertIsNone(allowed.request.headers.get('forwarded'))
        self.assertIsNone(allowed.request.headers.get('x-forwarded-host'))
        for refused in [flow('example.com', 80), flow('localhost', 3000, scheme='https')]:
            proxy.request(refused)
            self.assertEqual(refused.response.status_code, 451)

    def test_connect_tunnels_to_the_agent_only(self):
        tunnel = flow('localhost', 18000)
        proxy.http_connect(tunnel)
        self.assertEqual(tunnel.server_conn.address, ('agent-code-server', 18000))
        refused = flow('example.com', 443)
        proxy.http_connect(refused)
        self.assertEqual(refused.response.status_code, 451)
        self.assertIsNone(refused.server_conn.address)

    def answered(self, port, status, headers):
        sent = flow('localhost', port)
        proxy.request(sent)
        sent.response = Response(status, headers)
        proxy.response(sent)
        return sent.response

    def test_redirects_stay_among_the_origins(self):
        for location in ['/check', 'http://localhost:3001/', '?a=1', '//localhost:18000/']:
            self.assertEqual(self.answered(3000, 302, [('Location', location)]).status_code, 302, location)
        for location in ['http://example.com/', '//example.com/', 'https://localhost:3000/', 'http://127.0.0.1:3000/',
                         'vscode://x', 'http://localhost:99999/']:
            self.assertEqual(self.answered(3000, 302, [('Location', location)]).status_code, 451, location)
        self.assertEqual(self.answered(18000, 302, [('Location', 'http://example.com/')]).status_code, 451)

    def test_code_server_proxy_paths_redirect_to_app_origins(self):
        for prefix in ('/proxy', '/absproxy'):
            for suffix, app_path in [
                ('/', '/'),
                ('', '/'),
                ('?x=1', '/?x=1'),
                ('?', '/?'),
                ('/foo/?x=1', '/foo/?x=1'),
                ('/foo?x=1', '/foo?x=1'),
                ('/login?next=%2Fhome&x=1', '/login?next=%2Fhome&x=1'),
                ('//example.com/a', '//example.com/a'),
            ]:
                path = f'{prefix}/3000{suffix}'
                with self.subTest(path=path):
                    sent = flow('localhost', 18000)
                    sent.request.path = path
                    proxy.request(sent)
                    self.assertEqual(sent.response.status_code, 302)
                    self.assertEqual(sent.response.headers.get('location'), f'http://localhost:3000{app_path}')
                    self.assertEqual(sent.request.host, 'localhost')
            for suffix in ('0/', '65536/', 'example.com/', '3000@evil/', '3000?x=1\r\nInjected: yes'):
                path = f'{prefix}/{suffix}'
                with self.subTest(path=path):
                    sent = flow('localhost', 18000)
                    sent.request.path = path
                    proxy.request(sent)
                    self.assertEqual(sent.response.status_code, 451)
            app = flow('localhost', 3000)
            app.request.path = f'{prefix}/4000/login'
            proxy.request(app)
            self.assertIsNone(app.response)

    def test_apps_may_be_framed_and_the_ide_is_left_alone(self):
        headers = [('X-Frame-Options', 'SAMEORIGIN'),
                   ('Content-Security-Policy', "default-src 'self'; frame-ancestors 'self'"),
                   ('Content-Security-Policy', 'frame-ancestors none'),
                   ('Content-Security-Policy-Report-Only', "frame-ancestors 'self'")]
        app = self.answered(3000, 200, headers).headers
        self.assertIsNone(app.get('x-frame-options'))
        self.assertEqual(app.get_all('content-security-policy'), ["default-src 'self'"])
        self.assertEqual(app.get_all('content-security-policy-report-only'), ["frame-ancestors 'self'"])
        ide = self.answered(18000, 200, headers).headers
        self.assertEqual(ide.get('x-frame-options'), 'SAMEORIGIN')
        self.assertEqual(len(ide.get_all('content-security-policy')), 2)

    def test_frame_ancestors(self):
        self.assertEqual(proxy.frame_ancestors("frame-ancestors 'self'"), '')
        self.assertEqual(proxy.frame_ancestors("script-src 'self';FRAME-ANCESTORS x; img-src *"),
                         "script-src 'self'; img-src *")
        self.assertEqual(proxy.frame_ancestors("frame-ancestors a, default-src b"), 'default-src b')



if __name__ == '__main__':
    unittest.main()
