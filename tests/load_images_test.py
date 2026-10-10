"""How load-images.py reads .devcontainer/images.txt; no Docker needed."""
import importlib.util
from pathlib import Path
import subprocess
import unittest

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


if __name__ == '__main__':
    unittest.main()
