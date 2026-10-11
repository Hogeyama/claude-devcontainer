"""How load-images.py reads docker.images of claude.toml; no Docker needed."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('load_images', ROOT / '.devcontainer/claude/scripts/load-images.py')
load_images = importlib.util.module_from_spec(spec)
spec.loader.exec_module(load_images)


class ListTest(unittest.TestCase):
    def test_reads_references_without_comments_and_blank_lines(self):
        content = b'# header\n\npostgres:16  # database\n  testcontainers/ryuk:0.14.0\n'
        self.assertEqual(load_images.images(content), ['postgres:16', 'testcontainers/ryuk:0.14.0'])

    def test_marker_matches_the_name_wait_ready_computes(self):
        content = b'postgres:16\n'
        shell = subprocess.run(['sh', '-c', 'sha256sum | cut -c1-16'], input=content, capture_output=True, check=True)
        self.assertEqual(load_images.marker(content), 'images-' + shell.stdout.decode().strip())

    def test_list_is_what_wait_ready_reads(self):
        # Their marker names must agree: initializeCommand writes the file, and
        # load-images.py reads the settings, which may have changed since.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.devcontainer').mkdir()
            (root / '.devcontainer/claude.toml').write_text('[docker]\nimages = ["busybox:1.37", "postgres:16"]\n')
            with mock.patch.object(load_images, 'ROOT', root):
                listed = load_images.listed()
            settings_spec = importlib.util.spec_from_file_location('settings', ROOT / '.devcontainer/claude/scripts/settings.py')
            settings = importlib.util.module_from_spec(settings_spec)
            settings_spec.loader.exec_module(settings)
            settings.generate(root, {})
            self.assertEqual(listed, (root / '.devcontainer/.generated/images.txt').read_bytes())
            self.assertEqual(load_images.images(listed), ['busybox:1.37', 'postgres:16'])


if __name__ == '__main__':
    unittest.main()
