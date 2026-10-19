"""Where this workspace's files are: the repository, its settings, and the core.

In repository mode the core is .devcontainer/claude/ in the workspace, and both
are found from this file's place. In host mode the host command
(scripts/claude-devcontainer) runs an installed core for a repository without
.devcontainer, and names the repository and its state directory in the
environment; the state directory then stands in for .devcontainer/.
"""
import hashlib
import os
from pathlib import Path

# The core: .devcontainer/claude/, or an installed version under DATA.
CORE = Path(__file__).resolve().parents[1]
WORKSPACE = 'CLAUDE_DEVCONTAINER_WORKSPACE'
CONFIG_DIR = 'CLAUDE_DEVCONTAINER_CONFIG_DIR'
DATA = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share') / 'claude-devcontainer'
STATE = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'claude-devcontainer'


def host_mode(environ=os.environ):
    return bool(environ.get(CONFIG_DIR))


def workspace_root(environ=os.environ):
    value = environ.get(WORKSPACE)
    return Path(value) if value else CORE.parents[1]


def config_dir(root, environ=os.environ):
    """The directory of .generated/ and .nix-env/: .devcontainer/, or the host mode's state."""
    value = environ.get(CONFIG_DIR)
    return Path(value) if value else Path(root) / '.devcontainer'


def compose_file(root, environ=os.environ):
    return CORE / 'host/compose.yaml' if host_mode(environ) else config_dir(root, environ) / 'compose.yaml'


def workspace_id(root):
    return hashlib.sha256(os.fsencode(Path(root).resolve())).hexdigest()[:16]


def state_dir(root):
    return STATE / 'workspaces' / workspace_id(root)
