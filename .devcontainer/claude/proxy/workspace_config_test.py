"""Tests of the workspace's proxy.yaml; run with mitmproxy 11.1.3 installed."""
import base64
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


def host(name, body):
    return f'hosts:\n  {name}:\n' + ''.join(f'    {line}\n' for line in body.strip('\n').split('\n'))


class ParseTest(unittest.TestCase):
    def test_empty(self):
        for text in ['', '# nothing yet\n', 'hosts:\n', 'hosts: {}']:
            config = workspace_config.parse(text)
            self.assertEqual((config.routes, config.headers, config.inject), ({}, {}, {}))

    def test_routes(self):
        config = workspace_config.parse("""
hosts:
  fonts.googleapis.com:
    routes:
      - GET /css2?
  fonts.gstatic.com:
    routes:
      - /s/.+  # any method
      - HEAD /
""")
        self.assertEqual(config.routes, {'fonts.googleapis.com': [('GET', '/css2?')],
                                         'fonts.gstatic.com': [(None, '/s/.+'), ('HEAD', '/')]})
        self.assertEqual(config.secrets(), [])

    def test_inject_and_headers(self):
        config = workspace_config.parse("""
hosts:
  api.notion.com:
    routes:
      - /v1/.+
    headers:
      - Notion-Version
    inject:
      Authorization: Bearer ${NOTION_API_KEY}
      X-Pair: ${A}-${B_2}
  github.com:
    routes:
      - /[^/]+/[^/]+\\.git/info/refs
    inject:
      Authorization:
        basic: x-access-token:${GITHUB_TOKEN}
""")
        self.assertEqual(config.headers, {'api.notion.com': {'notion-version'}})
        self.assertEqual(config.secrets(), ['A', 'B_2', 'GITHUB_TOKEN', 'NOTION_API_KEY'])
        secrets = {'NOTION_API_KEY': 'n', 'A': 'a', 'B_2': 'b', 'GITHUB_TOKEN': 'pat'}
        rendered = {host: [(name, workspace_config.render(template, secrets)) for name, template in entries]
                    for host, entries in config.inject.items()}
        self.assertEqual(rendered, {
            'api.notion.com': [('Authorization', 'Bearer n'), ('X-Pair', 'a-b')],
            'github.com': [('Authorization', 'Basic ' + base64.b64encode(b'x-access-token:pat').decode())]})
        with self.assertRaises(KeyError):
            workspace_config.render(config.inject['github.com'][0][1], {})

    def test_secret_values_are_not_reinterpreted_as_templates_or_escapes(self):
        for value in ['${OTHER}', r'\1', r'\g<1>', '$\\']:
            with self.subTest(value=value):
                template = workspace_config.parse_template('prefix:${TOKEN}${TOKEN}:suffix', 'test')
                self.assertEqual(workspace_config.render(template, {'TOKEN': value}),
                                 'prefix:' + value + value + ':suffix')

    def test_template_rejects_invalid_placeholders_and_control_characters(self):
        for value in ['$TOKEN', '${TOKEN', '${lower}', '$${TOKEN}', '${TOKEN}\n', '\x00${TOKEN}', '\x7f']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                workspace_config.parse_template(value, 'test')

    def test_invalid(self):
        for text in ['hosts: [', '[]', 'routes: []', 'hosts: []', 'hosts:\n  a.example.com:\n',
                     'hosts:\n  a.example.com:\n    routes:\n      - /\n  a.example.com:\n    routes:\n      - /\n',
                     '!!python/object/apply:os.system [true]',
                     host('a.example.com', 'routes: []'),
                     host('a.example.com', 'routes:\n  - /\nport: 80'),
                     host('Example.com', 'routes:\n  - /'),
                     host('"example.com:8443"', 'routes:\n  - /'),
                     host('10.0.0.1', 'routes:\n  - /'),
                     host('localhost', 'routes:\n  - /'),
                     host('"*.example.com"', 'routes:\n  - /'),
                     host('a.example.com', 'routes:\n  - get /'),
                     host('a.example.com', 'routes:\n  - GET'),
                     host('a.example.com', 'routes:\n  - example'),
                     host('a.example.com', 'routes:\n  - /('),
                     host('a.example.com', 'routes:\n  - method: GET'),
                     host('a.example.com', 'routes:\n  - /\nheaders:\n  - Bad Name'),
                     host('a.example.com', 'routes:\n  - /\nheaders: X-A'),
                     host('a.example.com', 'routes:\n  - /\ninject:\n  - Authorization'),
                     host('a.example.com', 'routes:\n  - /\ninject:\n  Host: x'),
                     host('a.example.com', 'routes:\n  - /\ninject:\n  Content-Length: "1"'),
                     host('a.example.com', 'routes:\n  - /\ninject:\n  X-A: $HOME'),
                     host('a.example.com', 'routes:\n  - /\ninject:\n  X-A: ${lower}'),
                     host('a.example.com', 'routes:\n  - /\ninject:\n  X-A: ""'),
                     host('a.example.com', 'routes:\n  - /\ninject:\n  X-A:\n    basic: x\n    other: y'),
                     host('a.example.com', 'routes:\n  - /\ninject:\n  X-A:\n    - x')]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                workspace_config.parse(text)

    def test_unpassable_headers(self):
        for name in ['Authorization', 'cookie', 'X-Api-Key', 'Host', 'Proxy-Authorization', 'Upgrade']:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'use inject'):
                workspace_config.parse(host('a.example.com', f'routes:\n  - /\nheaders:\n  - {name}'))

    def test_authenticated_hosts_stay_fixed(self):
        for name in workspace_config.AUTHENTICATED_HOSTS:
            with self.subTest(host=name), self.assertRaisesRegex(ValueError, 'authenticated'):
                workspace_config.parse(host(name, 'routes:\n  - GET /v1/messages'))

    def test_template(self):
        config = workspace_config.parse((Path(__file__).parent / '../template/proxy.yaml').read_text()) \
            if (Path(__file__).parent / '../template/proxy.yaml').exists() else None
        if config is None:
            self.skipTest('template/proxy.yaml is not mounted')
        self.assertEqual(set(config.routes), {'api.github.com', 'github.com'})
        self.assertEqual(config.secrets(), ['GITHUB_TOKEN'])

    def test_missing_files_are_empty(self):
        self.assertEqual(workspace_config.installed('/nonexistent').routes, {})


class InstallTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_nothing_without_proxy_yaml(self):
        workspace_config.install(self.directory, self.directory / 'installed')
        self.assertFalse((self.directory / 'installed').exists())

    def test_installed(self):
        (self.directory / 'proxy.yaml').write_text(host('example.com', 'routes:\n  - GET /'))
        workspace_config.install(self.directory, self.directory / 'installed')
        self.assertEqual(workspace_config.installed(self.directory / 'installed').routes,
                         {'example.com': [('GET', '/')]})

    def test_local_only(self):
        (self.directory / 'proxy.local.yaml').write_text(host('redmine.example.com', 'routes:\n  - GET /issues\\.json'))
        workspace_config.install(self.directory, self.directory / 'installed')
        self.assertEqual(workspace_config.installed(self.directory / 'installed').routes,
                         {'redmine.example.com': [('GET', r'/issues\.json')]})

    def test_local_adds_and_replaces_hosts(self):
        (self.directory / 'proxy.yaml').write_text(
            host('example.com', 'routes:\n  - GET /\nheaders:\n  - X-Shared\ninject:\n  X-Key: ${SHARED}')
            + host('other.example.com', 'routes:\n  - GET /')[len('hosts:\n'):])
        (self.directory / 'proxy.local.yaml').write_text(
            host('example.com', 'routes:\n  - POST /api')
            + host('redmine.example.com', 'routes:\n  - /\ninject:\n  X-Redmine-API-Key: ${REDMINE_API_KEY}')[len('hosts:\n'):])
        workspace_config.install(self.directory, self.directory / 'installed')
        config = workspace_config.installed(self.directory / 'installed')
        self.assertEqual(config.routes, {'example.com': [('POST', '/api')], 'other.example.com': [('GET', '/')],
                                         'redmine.example.com': [(None, '/')]})
        # The local example.com replaces the shared one, headers and inject included.
        self.assertEqual(config.headers, {})
        self.assertEqual(list(config.inject), ['redmine.example.com'])
        self.assertEqual(config.secrets(), ['REDMINE_API_KEY'])

    def test_invalid_local_file_fails_and_is_named(self):
        (self.directory / 'proxy.yaml').write_text(host('example.com', 'routes:\n  - GET /'))
        (self.directory / 'proxy.local.yaml').write_text(host('api.anthropic.com', 'routes:\n  - /'))
        with self.assertRaisesRegex(ValueError, '^proxy.local.yaml: '):
            workspace_config.install(self.directory, self.directory / 'installed')
        self.assertFalse((self.directory / 'installed').exists())

    def test_invalid_file_is_not_installed(self):
        (self.directory / 'proxy.yaml').write_text(host('api.anthropic.com', 'routes:\n  - /'))
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
