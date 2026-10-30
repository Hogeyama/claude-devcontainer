#!/usr/bin/env python3
"""Load images into docker.shared_base, the DinD data that workspaces share.

Each workspace's DinD runs over the base with an overlay of its own
(settings.py), so what is loaded here is stored once for all of them, and a
workspace loads only what the base lacks. This runs a dockerd of its own, from
this workspace's DinD image, on the base itself.

The base is the overlays' lower directory, which must not change under them:
every DinD over it must be stopped first. A workspace sees the images added
later only until its own DinD has written the image list to its upper
directory; to see them after that, remove that workspace's upper and work
directories, with its DinD data, while it is stopped.

Usage: dind-base.py [IMAGE...]   (default: docker.images of this workspace)
"""
import importlib.util
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import layout  # noqa: E402
import settings  # noqa: E402

ROOT = layout.workspace_root()
# The storage of a DinD over a shared base (compose.yaml), which the base must share.
DOCKERD_ARGS = '--storage-driver=fuse-overlayfs --feature=containerd-snapshotter=false'
WAIT_SECONDS = 60


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parent / file)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


load_images = module('load_images', 'load-images.py')


def docker(*arguments, check=True):
    return subprocess.run(['docker', *arguments], check=check, capture_output=True, text=True)


def dind_image():
    """The image of this workspace's DinD container, running or not."""
    projects = docker('ps', '-a', '--filter', f'label=devcontainer.local_folder={ROOT}',
                      '--filter', 'label=com.docker.compose.service=agent',
                      '--format', '{{.Label "com.docker.compose.project"}}').stdout.split()
    for project in sorted(set(projects)):
        found = docker('ps', '-aq', '--filter', f'label=com.docker.compose.project={project}',
                       '--filter', 'label=com.docker.compose.service=dind').stdout.split()
        if found:
            return docker('inspect', '--format', '{{.Image}}', found[0]).stdout.strip()
    return None


def main():
    loaded = settings.load(ROOT)
    name = settings.shared_base(loaded)
    if not name:
        sys.exit('error: set docker.enabled and docker.shared_base in claude.toml first.')
    settings.prepare_dind(ROOT)
    users = docker('ps', '--filter', f'label=dev.claude-devcontainer.dind-base={name}',
                   '--format', '{{.Names}}').stdout.split()
    if users:
        sys.exit(f'error: {", ".join(users)} runs over the base {name}; stop every environment using it first.')
    image = dind_image()
    if not image:
        sys.exit('error: this workspace has no DinD container yet; run `devcontainer up` once, stop it, then retry.')
    references = sys.argv[1:] or load_images.images(load_images.listed())
    if not references:
        sys.exit('error: no images: name them, or list them in docker.images.')
    load_images.pull(references)
    uid, gid = loaded['agent']['uid'], loaded['agent']['gid']
    data = settings.dind_paths(name, ROOT)['data']
    command = (f"exec su -s /bin/sh -c 'exec dockerd-entrypoint.sh dockerd "
               f"--host=unix:///run/user/{uid}/docker.sock {DOCKERD_ARGS}' rootless")
    # No network: it only loads what the host sends.
    container = docker('run', '-d', '--rm', '--init', '--privileged', '--network', 'none', '--user', '0:0',
                       '-e', 'DOCKER_TLS_CERTDIR=', '-e', 'TINI_SUBREAPER=1',
                       '--tmpfs', f'/run/user/{uid}:uid={uid},gid={gid},mode=0700',
                       '--mount', f'type=bind,source={data},target={settings.DIND_DATA}',
                       '--entrypoint', 'sh', image, '-euc', command).stdout.strip()
    try:
        deadline = time.monotonic() + WAIT_SECONDS
        while load_images.in_dind(container, 'docker info >/dev/null 2>&1', check=False).returncode:
            if time.monotonic() > deadline:
                sys.exit(f'error: the base dockerd did not start within {WAIT_SECONDS}s; '
                         f'see docker logs {container[:12]}.')
            time.sleep(1)
        failures = load_images.load(container, references)
    finally:
        docker('stop', '--time', '30', container, check=False)
    if failures:
        sys.exit('\n'.join(failures))


if __name__ == '__main__':
    try:
        main()
    except ValueError as error:
        sys.exit(f'error: {error}')
    except subprocess.CalledProcessError as error:
        sys.exit(f'error: {" ".join(error.cmd[:2])}: {error.stderr.strip()}')
