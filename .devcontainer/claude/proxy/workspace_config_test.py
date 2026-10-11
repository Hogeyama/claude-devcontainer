"""Tests of the workspace's proxy.yaml; run with mitmproxy 11.1.3 installed."""
import datetime
from pathlib import Path
import tempfile
import unittest

import certifi
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

import workspace_config


def certificate(ca=True):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Company Root')])
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
               .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1)))
    if ca is not None:
        builder = builder.add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    return builder.sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM)


class ParseTest(unittest.TestCase):
    def test_empty(self):
        for text in ['', '# nothing yet\n', '{}']:
            self.assertEqual(workspace_config.parse(text), {})

    def test_routes(self):
        routes = workspace_config.parse('''
routes:
  - {method: GET, host: fonts.googleapis.com, path: /css2?}
  - {method: "*", host: fonts.gstatic.com, path: /s/.+}  # a comment
  - method: HEAD
    host: fonts.gstatic.com
    path: /
''')
        self.assertEqual(routes, {'fonts.googleapis.com': [('GET', '/css2?')],
                                  'fonts.gstatic.com': [(None, '/s/.+'), ('HEAD', '/')]})

    def test_invalid(self):
        route = '{method: GET, host: example.com, path: /}'
        for text in ['routes: [', '[]', 'routes: {}', 'other: 1', 'routes: []\nroutes: []',
                     '!!python/object/apply:os.system [true]',
                     'routes: [{method: GET, host: example.com, path: /, port: 80}]',
                     'routes: [{host: example.com, path: /}]',
                     'routes: [{method: GET, host: Example.com, path: /}]',
                     'routes: [{method: GET, host: "example.com:8443", path: /}]',
                     'routes: [{method: GET, host: 10.0.0.1, path: /}]',
                     'routes: [{method: GET, host: localhost, path: /}]',
                     'routes: [{method: GET, host: "*.example.com", path: /}]',
                     'routes: [{method: get, host: example.com, path: /}]',
                     'routes: [{method: null, host: example.com, path: /}]',
                     'routes: [{method: GET, host: example.com, path: example}]',
                     'routes: [{method: GET, host: example.com, path: /(}]',
                     f'routes: [{route}]\nca: [certs/company.pem]']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                workspace_config.parse(text)

    def test_authenticated_hosts_stay_fixed(self):
        for host in workspace_config.AUTHENTICATED_HOSTS:
            with self.subTest(host=host), self.assertRaisesRegex(ValueError, 'authenticated'):
                workspace_config.parse(f'routes: [{{method: GET, host: {host}, path: /gists}}]')

    def test_missing_file_adds_no_routes(self):
        self.assertEqual(workspace_config.routes('/nonexistent/proxy.yaml'), {})


class InstallTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_nothing_without_proxy_yaml(self):
        workspace_config.install(self.directory, self.directory / 'installed')
        self.assertFalse((self.directory / 'installed').exists())

    def test_routes(self):
        (self.directory / 'proxy.yaml').write_text('routes: [{method: GET, host: example.com, path: /}]')
        workspace_config.install(self.directory, self.directory / 'installed')
        self.assertEqual(workspace_config.routes(self.directory / 'installed/proxy.yaml'), {'example.com': [('GET', '/')]})

    def test_invalid_file_is_not_installed(self):
        (self.directory / 'proxy.yaml').write_text('routes: [{method: GET, host: github.com, path: /}]')
        with self.assertRaises(ValueError):
            workspace_config.install(self.directory, self.directory / 'installed')
        self.assertFalse((self.directory / 'installed').exists())


class UpstreamCaTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.ca = Path(temporary.name) / 'ca.pem'
        self.target = Path(temporary.name) / 'bundle/upstream-ca.pem'

    def bundle(self, data):
        self.ca.write_bytes(data)
        return workspace_config.upstream_ca(self.ca, self.target)

    def test_no_ca(self):
        self.assertIsNone(workspace_config.upstream_ca(self.ca, self.target))
        for data in [b'', b'\n']:
            self.assertIsNone(self.bundle(data))
        self.assertFalse(self.target.exists())

    def test_bundle_keeps_public_roots(self):
        company = certificate() + certificate()
        self.assertEqual(self.bundle(company), self.target)
        bundle = self.target.read_text()
        self.assertIn(Path(certifi.where()).read_text(), bundle)
        self.assertIn(company.decode(), bundle)

    def test_bad_ca(self):
        for data in [certificate(ca=False), certificate(ca=None), certificate() + certificate(ca=False),
                     b'not a certificate']:
            with self.subTest(data=data[:40]), self.assertRaises(ValueError):
                self.bundle(data)


if __name__ == '__main__':
    unittest.main()
