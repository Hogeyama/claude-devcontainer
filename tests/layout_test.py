"""Where the scripts find the workspace, its settings and the core."""
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('layout', ROOT / '.devcontainer/claude/scripts/layout.py')
layout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(layout)


class LayoutTest(unittest.TestCase):
    def test_repository_mode_is_found_from_the_core(self):
        self.assertEqual(layout.CORE, ROOT / '.devcontainer/claude')
        self.assertFalse(layout.host_mode({}))
        self.assertEqual(layout.workspace_root({}), ROOT)
        self.assertEqual(layout.config_dir(ROOT, {}), ROOT / '.devcontainer')
        self.assertEqual(layout.compose_file(ROOT, {}), ROOT / '.devcontainer/compose.yaml')

    def test_relative_root_stays_relative(self):
        self.assertEqual(layout.config_dir(Path('.'), {}), Path('.devcontainer'))

    def test_host_mode_takes_both_from_the_environment(self):
        environ = {layout.WORKSPACE: '/repo', layout.CONFIG_DIR: '/state/x'}
        self.assertTrue(layout.host_mode(environ))
        self.assertEqual(layout.workspace_root(environ), Path('/repo'))
        self.assertEqual(layout.config_dir(Path('/repo'), environ), Path('/state/x'))
        self.assertEqual(layout.compose_file(Path('/repo'), environ), layout.CORE / 'host/compose.yaml')

    def test_workspace_id_names_the_real_path(self):
        with tempfile.TemporaryDirectory() as directory:
            real = Path(directory) / 'repo'
            real.mkdir()
            link = Path(directory) / 'link'
            link.symlink_to(real)
            expected = hashlib.sha256(os.fsencode(real.resolve())).hexdigest()[:16]
            self.assertEqual(layout.workspace_id(link), expected)
            self.assertEqual(layout.state_dir(link), layout.STATE / 'workspaces' / expected)


if __name__ == '__main__':
    unittest.main()
