#!/usr/bin/env python3
r"""The Windows client: the editor's Electron shell (../shell/) on Windows, opened from WSL.

scripts/code-server.sh runs this in WSL, where the host's scripts and Docker
run, with the agent's server up and its connection token in
CLAUDE_DEVCONTAINER_VSCODE_TOKEN. The shell runs on Windows and reaches
code-server-proxy on the 127.0.0.1 port that WSL forwards from its own. No
administrator rights are used, so nothing outside the shell confines it: the
shell's own enforcement is the boundary, as on Linux (README.md).

The Windows side, under %LOCALAPPDATA%\ClaudeDevcontainer, which the agent
cannot reach:
  electron\<version>-win32-x64   Electron, from ../electron.py
  workspaces\<hash>\shell        the shell, copied at each open
  workspaces\<hash>\profile      its profile: the editor's user settings and state
  workspaces\<hash>\client.log   its output
One profile per workspace, so that several can be open at once; the shell
keeps one window per profile.

Usage:  client.py --proxy-port PORT [--folder /workspace]   open the window
        client.py --prepare                                download Electron
"""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
SHELL = HERE.parent / 'shell'
TOKEN = 'CLAUDE_DEVCONTAINER_VSCODE_TOKEN'
PLATFORM = 'win32-x64'

sys.path.insert(0, str(HERE.parent))
import electron  # noqa: E402
sys.path.insert(0, str(HERE.parents[1] / 'scripts'))
import layout  # noqa: E402


def fail(message):
    raise SystemExit('error: ' + message)


def wslpath(option, path):
    return subprocess.run(['wslpath', option, str(path)], capture_output=True, text=True, check=True).stdout.strip()


def base_directory():
    """%LOCALAPPDATA%\\ClaudeDevcontainer, as a WSL path."""
    result = subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
         "[Environment]::GetFolderPath('LocalApplicationData')"],
        capture_output=True, text=True)
    if result.returncode or not result.stdout.strip():
        fail(f'cannot find %LOCALAPPDATA% through powershell.exe: {result.stderr.strip()}')
    return Path(wslpath('-u', result.stdout.strip())) / 'ClaudeDevcontainer'


def workspace_directory(base, root=None):
    # The repository, also in host mode, where the core is installed elsewhere.
    root = Path(root or layout.workspace_root()).resolve()
    return base / 'workspaces' / hashlib.sha256(os.fsencode(root)).hexdigest()[:24]


def shell_command(executable, shell, profile, port, folder):
    """Electron's command line, with Windows paths."""
    return [str(executable), wslpath('-w', shell), f'--proxy-port={port}', f'--folder={folder}',
            f'--user-data-dir={wslpath("-w", profile)}']


def open_window(port, folder):
    token = os.environ.get(TOKEN, '')
    if not token:
        fail(f'no connection token in {TOKEN}; open the editor with code-server.sh')
    base = base_directory()
    directory = electron.ensure(base / 'electron', PLATFORM)
    state = workspace_directory(base)
    state.mkdir(parents=True, exist_ok=True)
    # Over the previous copy: a window that is open has read its files.
    shutil.copytree(SHELL, state / 'shell', dirs_exist_ok=True)
    # WSL passes a variable to a Windows process only when WSLENV names it;
    # /w: from WSL to Windows only.
    names = [name for name in os.environ.get('WSLENV', '').split(':') if name]
    environ = os.environ | {'WSLENV': ':'.join(names + [TOKEN + '/w'])}
    command = shell_command(directory / electron.executable(PLATFORM), state / 'shell', state / 'profile', port, folder)
    with open(state / 'client.log', 'w') as log:
        subprocess.Popen(command, cwd=state, env=environ, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         start_new_session=True)
    print(f"the editor is opening; its output goes to {state / 'client.log'}")


def main():
    parser = argparse.ArgumentParser(description="Open the agent's workspace in the editor shell on Windows.")
    parser.add_argument('--proxy-port', type=int, help="the port of this workspace's code-server-proxy")
    parser.add_argument('--folder', default='/workspace', help='the folder in the agent to open')
    parser.add_argument('--prepare', action='store_true', help='download Electron only')
    args = parser.parse_args()
    try:
        if args.prepare:
            print(f'electron: {electron.ensure(base_directory() / "electron", PLATFORM)}')
        elif args.proxy_port:
            open_window(args.proxy_port, args.folder)
        else:
            parser.error('--proxy-port or --prepare is required')
    except (OSError, subprocess.CalledProcessError) as error:
        sys.exit(f'error: {error}')


if __name__ == '__main__':
    main()
