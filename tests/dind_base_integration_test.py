"""docker.shared_base end to end: dind-base.py adds generations, DinDs over them name their images.

Needs Docker that runs privileged containers, and pulls busybox, alpine and
hello-world. Opt in with RUN_DOCKER_TESTS=1. Rootless dockerd in DinD needs
/dev/net/tun and /dev/fuse usable by others (0666), as on common hosts.

dind-base.py runs as the host's user, never root, which could do what the
user cannot (move DinD's directories, say): run as root, the test runs it as
HOST_UID (1000), who must be able to use the Docker socket.
"""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / '.devcontainer/claude/scripts'
DATA = '/home/rootless/.local/share/docker'
HOST_UID = int(os.environ.get('HOST_UID', '1000'))


def docker(*arguments, check=True):
    return subprocess.run(['docker', *map(str, arguments)], capture_output=True, text=True, check=check)


@unittest.skipUnless(os.environ.get('RUN_DOCKER_TESTS') == '1', 'Docker integration disabled; set RUN_DOCKER_TESTS=1')
class SharedBaseTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='cdc-dind-base-')
        self.top = Path(directory.name)
        # DinD's files are its own users'; remove them as root.
        self.addCleanup(lambda: docker('run', '--rm', '-v', f'{self.top}:/t', 'busybox:1.36',
                                       'sh', '-c', 'rm -rf /t/* /t/.[!.]*', check=False))
        self.addCleanup(directory.cleanup)
        self.repo = self.top / 'repo'
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        subprocess.run(['git', '-C', str(self.repo), '-c', 'user.name=t', '-c', 'user.email=t@example.com',
                        'commit', '--allow-empty', '-qm', 'fixture'], check=True)
        config = self.top / 'config/claude-devcontainer'
        config.mkdir(parents=True)
        (config / 'claude.toml').write_text('[docker]\nenabled = true\nshared_base = "common"\n'
                                            'images = ["busybox:1.36"]\n')
        # Host mode, so that the settings are the global claude.toml above.
        self.env = dict(os.environ, XDG_CONFIG_HOME=str(self.top / 'config'), XDG_STATE_HOME=str(self.top / 'state'),
                        CLAUDE_DEVCONTAINER_WORKSPACE=str(self.repo),
                        CLAUDE_DEVCONTAINER_CONFIG_DIR=str(self.top / 'workspace-state'))
        if os.geteuid() == 0:
            subprocess.run(['chown', '-R', f'{HOST_UID}:{HOST_UID}', str(self.top)], check=True)
        patcher = mock.patch.dict(os.environ, self.env)
        patcher.start()
        self.addCleanup(patcher.stop)
        sys.path.insert(0, str(SCRIPTS))
        self.addCleanup(sys.path.remove, str(SCRIPTS))
        # Fresh modules, which read the environment above as they load.
        for name in ('layout', 'settings', 'load-images'):
            sys.modules.pop(name, None)
        self.settings = importlib.import_module('settings')
        self.load_images = importlib.import_module('load-images')
        for image in ('busybox:1.36', 'alpine:3.20', 'hello-world:latest'):
            if docker('image', 'inspect', image, check=False).returncode:
                docker('pull', '-q', image)

    def dind_base(self, *arguments):
        env, user = self.env, []
        if os.geteuid() == 0:
            user = ['setpriv', f'--reuid={HOST_UID}', f'--regid={HOST_UID}', '--clear-groups']
            home = self.top / 'home'
            home.mkdir(exist_ok=True)
            os.chown(home, HOST_UID, HOST_UID)
            env = dict(env, HOME=str(home))
        result = subprocess.run([*user, sys.executable, SCRIPTS / 'dind-base.py', *arguments], env=env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def generations(self, paths):
        return [gen.name for gen in self.settings.dind_generations(paths)]

    def start_dind(self, loaded, image, tag):
        """This workspace's DinD as compose starts it: over the newest generations' overlay volume."""
        compose = json.loads(self.settings.workspace_compose(loaded, self.repo).replace('$$', '$'))
        (_, options), = compose['volumes'].items()
        volume = f'cdc-dind-base-test-{os.getpid()}-{tag}'
        docker('volume', 'create', '--driver', 'local', '--opt', 'type=overlay', '--opt', 'device=overlay',
               '--opt', 'o=' + options['driver_opts']['o'], volume)
        self.addCleanup(docker, 'volume', 'rm', '-f', volume, check=False)
        ids = self.settings.dind_ids(loaded)
        uid = ids['DIND_UID']
        command = (f'chown {uid}:{ids["DIND_GID"]} {DATA}; exec su -s /bin/sh -c "exec dockerd-entrypoint.sh dockerd '
                   f'--host=unix:///run/user/{uid}/docker.sock {ids["DIND_ARGS"]} '
                   '--storage-driver=fuse-overlayfs --feature=containerd-snapshotter=false" rootless')
        container = docker('run', '-d', '--privileged', '--init', '--user', '0:0', '-e', 'DOCKER_TLS_CERTDIR=',
                           '-e', 'TINI_SUBREAPER=1', '--tmpfs', f'/run/user/{uid}:uid={uid},gid=1000,mode=0750',
                           '--mount', f'type=volume,source={volume},target={DATA}',
                           '--entrypoint', 'sh', image, '-euc', command).stdout.strip()
        self.addCleanup(docker, 'rm', '-f', container, check=False)
        deadline = time.monotonic() + 60
        while self.load_images.in_dind(container, 'docker info >/dev/null 2>&1', check=False).returncode:
            self.assertLess(time.monotonic(), deadline, docker('logs', container, check=False).stderr[-2000:])
            time.sleep(1)
        return container

    def names(self, dind):
        output = self.load_images.in_dind(dind, 'docker images --format "{{.Repository}}:{{.Tag}}"').stdout
        return set(output.split())

    def test_generations_added_while_dind_runs_are_named_when_it_restarts(self):
        # up: docker.images into the first generation; a second up adds nothing.
        self.dind_base('--auto')
        self.dind_base('--auto')
        loaded = self.settings.load(self.repo)
        paths = self.settings.dind_paths(self.settings.dind_store(loaded), self.repo)
        self.assertEqual(self.generations(paths), ['1', '0'])
        self.assertEqual(set(json.loads(paths['images'].read_text())), {'busybox:1.36'})
        image = docker('build', '-q', *(f'--build-arg={key}={value}' for key, value in {
            'ROOTLESS_UID': '2000', 'ROOTLESS_GID': '2000', 'AGENT_UID': '1000', 'AGENT_GID': '1000',
            'MAP_AGENT_IDS': '1'}.items()), ROOT / '.devcontainer/claude/dind').stdout.strip()

        # A workspace names an image of its own, so its image list hides the base's.
        dind = self.start_dind(loaded, image, 'a')
        self.assertEqual(self.names(dind), {'busybox:1.36'})
        self.assertEqual(self.load_images.load(dind, ['busybox:1.36', 'alpine:3.20']), [])
        self.assertTrue((paths['upper'] / 'image/fuse-overlayfs/repositories.json').exists())

        # Another image goes into the base while that DinD runs, which goes on.
        self.dind_base('hello-world:latest')
        self.assertEqual(self.generations(paths), ['2', '1', '0'])
        # Nothing of the loaders is left behind.
        self.assertEqual(sorted(path.name for path in paths['gens'].iterdir()), ['0', '1', '2'])
        ran = self.load_images.in_dind(dind, 'docker run --rm busybox:1.36 echo ran', check=False)
        self.assertEqual(ran.stdout.strip(), 'ran', ran.stderr)
        docker('stop', '--time', '30', dind)

        # Restarted over the new generation, it names hello-world and loads nothing again.
        dind = self.start_dind(loaded, image, 'b')
        with mock.patch.object(self.load_images.subprocess, 'Popen', wraps=subprocess.Popen) as save:
            self.assertEqual(self.load_images.load(dind, ['busybox:1.36', 'alpine:3.20']), [])
        self.assertFalse([call for call in save.call_args_list if call.args[0][:2] == ['docker', 'save']])
        self.assertEqual(self.names(dind), {'busybox:1.36', 'alpine:3.20', 'hello-world:latest'})
        for name in ('busybox:1.36', 'alpine:3.20'):
            ran = self.load_images.in_dind(dind, 'docker run --rm "$1" echo ran', name, check=False)
            self.assertEqual(ran.stdout.strip(), 'ran', (name, ran.stderr))
        ran = self.load_images.in_dind(dind, 'docker run --rm hello-world:latest', check=False)
        self.assertIn('Hello from Docker!', ran.stdout, ran.stderr)


if __name__ == '__main__':
    unittest.main()
