#!/usr/bin/env python3
"""Add images to docker.shared_base, the DinD data that workspaces share.

The base is a stack of generations (settings.dind_paths). This makes a new
one that holds the images the base lacks: a dockerd of its own, from the DinD
image that it builds as compose does, loads them over the generations below.
Those never change, so the DinDs that run over them go on as they are, and
each takes the new generation when it next starts; nothing needs stopping.

`up` runs this with --auto for docker.images (initialize.sh), before DinD
starts. Run it by hand to add other images.

Usage: dind-base.py [IMAGE...]   (default: docker.images of this workspace)
       dind-base.py --auto       as `up` does: nothing without docker.shared_base
"""
import fcntl
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import layout  # noqa: E402

ROOT = layout.workspace_root()
# The storage of a DinD over a shared base (compose.yaml), which the base must share.
DOCKERD_ARGS = '--storage-driver=fuse-overlayfs --feature=containerd-snapshotter=false'
WAIT_SECONDS = 60
# The loader: DinD's data an overlay of the new generation over the others,
# with dockerd as compose.yaml runs it. All of gens/ is mounted, at /srv/gens:
# the overlay's upper and work directories must share a mount, and the new
# generation must be made in gens/ itself. Its top directory becomes DinD's
# (chown), and moving a directory to another parent needs write access to it,
# which the host's user then lacks; a rename in the same directory does not.
# On docker stop, it stops dockerd and removes the work directory, and with
# /srv/discard the new generation too: their files are DinD's, which the host's
# user may not remove.
LOADER = '''set -eu
data=/home/rootless/.local/share/docker
# Named here: in stop, $1, $2, ... are its own arguments.
new=/srv/gens/$2 work=/srv/gens/$3
mount -t overlay overlay -o "lowerdir=$1,upperdir=$new,workdir=$work" "$data"
chown "$4" "$data"
su -s /bin/sh -c "exec dockerd-entrypoint.sh dockerd --host=unix:///run/user/$5/docker.sock $6" rootless &
dockerd=$!
stop() {
    # All of rootless dockerd: rootlesskit, containerd and dockerd outlive su,
    # and hold the overlay until they exit.
    pkill -TERM -u rootless || true
    wait "$dockerd" || true
    i=0
    while pkill -0 -u rootless && [ "$i" -lt 60 ]; do
        sleep 1
        i=$((i + 1))
    done
    umount "$data"
    rm -rf "$work"
    if [ -e /srv/discard ]; then
        rm -rf "$new"
    fi
    exit 0
}
trap stop TERM INT
wait "$dockerd"
'''

settings = importlib.import_module('settings')
load_images = importlib.import_module('load-images')


def docker(*arguments, check=True):
    return subprocess.run(['docker', *arguments], check=check, capture_output=True, text=True)


def build(loaded):
    """This workspace's DinD image, built as compose builds it, and its ID."""
    ids = settings.dind_ids(loaded)
    args = {'ROOTLESS_UID': ids['DIND_UID'], 'ROOTLESS_GID': ids['DIND_GID'],
            'AGENT_UID': str(loaded['agent']['uid']), 'AGENT_GID': str(loaded['agent']['gid']),
            'MAP_AGENT_IDS': ids['DIND_MAP']}
    command = ['docker', 'build', '--quiet', *(f'--build-arg={key}={value}' for key, value in args.items()),
               str(layout.CORE / 'dind')]
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout.strip()


def read_record(path):
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def missing(references, record):
    """What the base lacks of references, pulled on the host; and the failures."""
    load_images.pull(references)
    lacking, failures = [], []
    for image in references:
        identity = load_images.host_id(image)
        if not identity:
            failures.append(f'{image}: not on the host; docker pull failed.')
        elif not isinstance(record.get(image), dict) or record[image].get('host') != identity:
            lacking.append(image)
    return lacking, failures


def add_generation(loaded, paths, references, record):
    """Load references into a new generation over the others; return the failures."""
    ids = settings.dind_ids(loaded)
    uid = ids['DIND_UID']
    image = build(loaded)
    below = settings.dind_generations(paths)
    number = int(below[0].name) + 1
    # In gens/, beside the generations, so that the new one takes its place by
    # a rename in the same directory (LOADER).
    new = paths['gens'] / f'.new-{number}-{os.getpid()}'
    work = paths['gens'] / f'.work-{number}-{os.getpid()}'
    new.mkdir()
    work.mkdir()
    lowerdir = ':'.join(f'/srv/gens/{gen.name}' for gen in below)
    # No network: it only loads what the host sends.
    container = docker('run', '-d', '--rm', '--init', '--privileged', '--network', 'none', '--user', '0:0',
                       '-e', 'DOCKER_TLS_CERTDIR=', '-e', 'TINI_SUBREAPER=1',
                       '--tmpfs', f'/run/user/{uid}:uid={uid},gid={loaded["agent"]["gid"]},mode=0750',
                       '--mount', f'type=bind,source={paths["gens"]},target=/srv/gens',
                       '--entrypoint', 'sh', image, '-euc', LOADER, 'loader', lowerdir, new.name, work.name,
                       f'{uid}:{ids["DIND_GID"]}', uid, f'{ids["DIND_ARGS"]} {DOCKERD_ARGS}').stdout.strip()
    failures, done = list(references), False
    try:
        deadline = time.monotonic() + WAIT_SECONDS
        while load_images.in_dind(container, 'docker info >/dev/null 2>&1', check=False).returncode:
            if time.monotonic() > deadline:
                logs = docker('logs', '--tail', '20', container, check=False)
                raise RuntimeError(f'the base dockerd did not start within {WAIT_SECONDS}s: '
                                   f'{(logs.stdout + logs.stderr).strip()}')
            time.sleep(1)
        # The loader sees the whole base already, names and all.
        failures = load_images.load(container, references, base={})
        recorded = read_json(load_images.in_dind(container, f'cat {load_images.RECORD}', check=False).stdout)
        for image in references:
            inspect = load_images.in_dind(container, 'docker image inspect --format "{{.Id}}" "$1"', image, check=False)
            if image in recorded and inspect.returncode == 0:
                record[image] = {'host': recorded[image], 'dind': inspect.stdout.strip()}
        done = len(failures) < len(references)
    finally:
        if not done:
            docker('exec', container, 'touch', '/srv/discard', check=False)
        docker('stop', '--time', '60', container, check=False)
    if not done:
        return failures
    new.rename(paths['gens'] / str(number))
    temporary = paths['images'].with_name(paths['images'].name + '.tmp')
    temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
    temporary.replace(paths['images'])
    added = [image for image in references if not any(f.startswith(image + ': ') for f in failures)]
    print(f'Added generation {number} to the DinD base {paths["base"].name}: {", ".join(added)}',
          file=sys.stderr, flush=True)
    return failures


def read_json(text):
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def main():
    auto = sys.argv[1:2] == ['--auto']
    names = [] if auto else sys.argv[1:]
    loaded = settings.load(ROOT)
    name = settings.dind_store(loaded)
    if not name:
        if auto:
            return
        sys.exit('error: set docker.enabled and docker.shared_base in claude.toml first.')
    references = names or load_images.images(load_images.listed())
    if not references:
        if auto:
            return
        sys.exit('error: no images: name them, or list them in docker.images.')
    settings.prepare_dind(loaded, ROOT)
    paths = settings.dind_paths(name, ROOT)
    # One generation at a time: two workspaces may start at once.
    with open(paths['lock'], 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        record = read_record(paths['images'])
        lacking, failures = missing(references, record)
        if lacking:
            failures += add_generation(loaded, paths, lacking, record)
    if failures:
        sys.exit('\n'.join(failures))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError) as error:
        sys.exit(f'error: {error}')
    except subprocess.CalledProcessError as error:
        sys.exit(f'error: {" ".join(error.cmd[:2])}: {(error.stderr or "").strip()}')
