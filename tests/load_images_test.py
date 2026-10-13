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


class StartupTest(unittest.TestCase):
    def run_startup(self, recreate_dind):
        clock = [0]
        marks = []
        def sleep(seconds):
            clock[0] += 10
        def find(service):
            if service == 'agent':
                return 'old-agent' if clock[0] < 90 else 'new-agent'
            return 'new-dind' if recreate_dind and clock[0] >= 90 else 'old-dind'
        def ready(dind, *args, **kwargs):
            return subprocess.CompletedProcess([], 1 if dind == 'new-dind' and clock[0] < 110 else 0)
        def finish(agent, content, failures=None):
            marks.append((clock[0], agent, failures))
        with mock.patch.object(load_images, 'WAIT_SECONDS', 180), \
             mock.patch.object(load_images.time, 'monotonic', side_effect=lambda: clock[0]), \
             mock.patch.object(load_images.time, 'sleep', side_effect=sleep), \
             mock.patch.object(load_images, 'find', side_effect=find), \
             mock.patch.object(load_images, 'in_dind', side_effect=ready), \
             mock.patch.object(load_images, 'load', return_value=[]) as load, \
             mock.patch.object(load_images, 'finish', side_effect=finish):
            load_images.wait_and_load(b'busybox:1.37\n')
        return marks, load.call_args_list

    def test_build_longer_than_a_minute_still_marks_the_replacement_agent(self):
        marks, loads = self.run_startup(False)
        self.assertEqual(marks, [(0, 'old-agent', None), (0, 'old-agent', []),
                                 (90, 'new-agent', None), (90, 'new-agent', [])])
        self.assertEqual(loads, [mock.call('old-dind', ['busybox:1.37'])])

    def test_replacement_dind_is_checked_before_reporting_completion(self):
        marks, loads = self.run_startup(True)
        self.assertEqual(marks, [(0, 'old-agent', None), (0, 'old-agent', []),
                                 (90, 'new-agent', None), (110, 'new-agent', [])])
        self.assertEqual(loads, [mock.call('old-dind', ['busybox:1.37']),
                                 mock.call('new-dind', ['busybox:1.37'])])


if __name__ == '__main__':
    unittest.main()
