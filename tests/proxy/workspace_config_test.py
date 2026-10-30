"""Tests of the installed proxy.json and the host's CA; run with mitmproxy 11.1.3 installed."""
import datetime
import json
from pathlib import Path
import tempfile
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.devcontainer/claude/proxy'))

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


def proxy_json(hosts):
    return json.dumps({'hosts': hosts})


class InstallTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.source = self.directory / 'proxy.json'

    def test_installed(self):
        self.source.write_text(proxy_json({
            'example.com': {'routes': ['GET /'], 'inject': {'Authorization': {'basic': 'x-access-token:${TOKEN}'}}}}))
        workspace_config.install(self.source, self.directory / 'installed')
        config = workspace_config.installed(self.directory / 'installed')
        self.assertEqual(config.routes, {'example.com': [('GET', '/')]})
        self.assertEqual(config.secrets(), ['TOKEN'])

    def test_no_hosts(self):
        for text in ['{}', proxy_json({})]:
            with self.subTest(text=text):
                self.assertEqual(workspace_config.parse(text).routes, {})

    def test_missing_file_fails(self):
        with self.assertRaises(FileNotFoundError):
            workspace_config.install(self.source, self.directory / 'installed')
        self.assertFalse((self.directory / 'installed').exists())

    def test_invalid_file_is_not_installed(self):
        for text in [proxy_json({'api.anthropic.com': {'routes': ['/']}}), '', '{', '[]', '{"hosts": {}, "other": 1}']:
            with self.subTest(text=text):
                self.source.write_text(text)
                with self.assertRaisesRegex(ValueError, '^proxy.json: '):
                    workspace_config.install(self.source, self.directory / 'installed')
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
