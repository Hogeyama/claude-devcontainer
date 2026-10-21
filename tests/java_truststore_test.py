"""java-truststore.py's JKS; keytool checks it when a JDK is installed."""
import base64
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('java_truststore', ROOT / '.devcontainer/claude/agent/java-truststore.py')
java_truststore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(java_truststore)


def pem(der):
    body = base64.b64encode(der).decode()
    return '-----BEGIN CERTIFICATE-----\n' + '\n'.join(body[i:i + 64] for i in range(0, len(body), 64)) \
        + '\n-----END CERTIFICATE-----\n'


def certificate(directory, name):
    """A self-signed CA's DER, made with openssl."""
    key, out = Path(directory) / f'{name}.key', Path(directory) / f'{name}.der'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', key, '-outform', 'DER',
                    '-out', out, '-days', '1', '-subj', f'/CN={name}'], check=True, capture_output=True)
    return out.read_bytes()


class TruststoreTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory)
        self.ders = [certificate(self.directory, 'one'), certificate(self.directory, 'proxy')]
        self.bundle = Path(self.directory) / 'bundle.crt'
        self.bundle.write_text('# comment\n' + pem(self.ders[0]) + '\n' + pem(self.ders[1]))
        self.target = Path(self.directory) / 'java/cacerts'

    def test_reads_every_certificate_of_the_bundle(self):
        self.assertEqual(java_truststore.certificates(self.bundle.read_bytes()), self.ders)

    def test_writes_trusted_entries_with_the_digest(self):
        java_truststore.write(self.bundle, self.target)
        data = self.target.read_bytes()
        self.assertEqual(oct(os.stat(self.target).st_mode & 0o777), '0o644')
        body, digest = data[:-20], data[-20:]
        self.assertEqual(struct.unpack('>IiI', body[:12]), (0xFEEDFEED, 2, 2))
        self.assertEqual(digest, hashlib.sha1('changeit'.encode('utf-16-be') + b'Mighty Aphrodite' + body).digest())
        for der in self.ders:
            self.assertIn(struct.pack('>i', len(der)) + der, body)

    def test_an_empty_bundle_is_an_error(self):
        self.bundle.write_text('')
        with self.assertRaises(ValueError):
            java_truststore.write(self.bundle, self.target)
        self.assertFalse(self.target.exists())

    @unittest.skipUnless(shutil.which('keytool'), 'no JDK')
    def test_keytool_reads_it(self):
        java_truststore.write(self.bundle, self.target)
        environment = {key: value for key, value in os.environ.items() if key != 'JAVA_TOOL_OPTIONS'}
        listed = subprocess.run(['keytool', '-list', '-keystore', self.target, '-storepass', 'changeit'],
                                capture_output=True, text=True, check=True, env=environment).stdout
        self.assertIn('Keystore type: JKS', listed)
        self.assertIn('contains 2 entries', listed)
        self.assertEqual(listed.count('trustedCertEntry'), 2)


if __name__ == '__main__':
    unittest.main()
