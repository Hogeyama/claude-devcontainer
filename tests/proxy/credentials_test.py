import json
from pathlib import Path
import tempfile
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.devcontainer/claude/proxy'))

import credentials

VALID = dict(anthropic_token='api', auth_mode='api-key')


class CredentialsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'credentials.json'

    def install(self, value):
        credentials.install_snapshot(json.dumps(value).encode(), self.path)

    def test_install_and_rotate(self):
        self.install(VALID)
        self.assertEqual(credentials.read_snapshot(self.path), ('api', 'api-key'))
        self.assertEqual(credentials.read_secrets(self.path), {})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o400)
        self.install(dict(anthropic_token='oauth', auth_mode='oauth', secrets={'GITHUB_TOKEN': 'pat'}))
        self.assertEqual(credentials.read_snapshot(self.path), ('oauth', 'oauth'))
        self.assertEqual(credentials.read_secrets(self.path), {'GITHUB_TOKEN': 'pat'})

    def test_invalid_input_keeps_previous_credentials(self):
        self.install(VALID)
        for bad in [{}, [], {**VALID, 'extra': 'x'}, {**VALID, 'auth_mode': 'other'}, {**VALID, 'anthropic_token': ''},
                    {**VALID, 'github_token': 'pat'}, {**VALID, 'secrets': []}, {**VALID, 'secrets': {'lower': 'x'}},
                    {**VALID, 'secrets': {'TOKEN': 1}}, {**VALID, 'secrets': {'TOKEN': 'a\r\nInjected: 1'}}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.install(bad)
        with self.assertRaises(ValueError):
            credentials.install_snapshot(b'{', self.path)
        self.assertEqual(credentials.read_snapshot(self.path), ('api', 'api-key'))
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])


if __name__ == '__main__':
    unittest.main()
