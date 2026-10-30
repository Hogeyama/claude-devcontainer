#!/usr/bin/env python3
"""Load the images of docker.images in .devcontainer/claude.toml into this workspace's DinD.

DinD cannot reach a registry, so the host pulls the images and copies them in.
The list lives in .devcontainer/, which the agent cannot write: the host pulls
whatever it names, so a name the agent chose would make the host contact a
registry of the agent's choosing. The list is read from the settings as
scripts/settings.py writes it to .generated/images.txt, one per line, so that a
change after `up` is loaded too.

Without arguments, load now. With --background, as the Dev Containers
initializeCommand runs it before the containers start, pull now and hand the
loading to a detached process that runs once DinD is up. Either way, the agent's
postStartCommand (wait-ready.sh) waits for the marker this writes into the agent.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import layout  # noqa: E402
import host_runtime  # noqa: E402
from host_runtime import log  # noqa: E402
import settings  # noqa: E402

# The workspace root (scripts/layout.py).
ROOT = layout.workspace_root()
LOG = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'claude-devcontainer/load-images.log'
# Kept in DinD's data volume, so that images survive rebuilds without being sent
# again. DinD's own image IDs cannot be compared: it uses the containerd image
# store, whose IDs differ from the host's for the same image.
RECORD = '/home/rootless/.local/share/docker/claude-devcontainer-images.json'
# Named after the list's content; wait-ready.sh computes the same name.
MARKERS = '/run/claude-devcontainer'
WAIT_SECONDS = 600
DIND_DOCKER = 'export DOCKER_HOST=unix:///run/user/$(id -u)/docker.sock; '


def listed():
    """The list as settings.py writes it to .generated/images.txt, which wait-ready.sh reads."""
    try:
        return settings.images_txt(settings.load(ROOT)).encode()
    except ValueError as error:
        sys.exit(f'error: {error}')


def images(content):
    """Return the image references in the list, without comments and blank lines."""
    lines = (line.split('#', 1)[0].strip() for line in content.decode(errors='replace').splitlines())
    return [line for line in lines if line]


def marker(content):
    return 'images-' + hashlib.sha256(content).hexdigest()[:16]


def docker(*arguments, data=None, check=True, stderr=None):
    return subprocess.run(['docker', *arguments], input=data, stdout=subprocess.PIPE, stderr=stderr, text=True,
                          check=check)


def host_id(image):
    result = docker('image', 'inspect', '--format', '{{.Id}}', image, check=False, stderr=subprocess.DEVNULL)
    return result.stdout.strip() if result.returncode == 0 else None


def pull(references):
    """Pull what the host lacks; keep what it has, even if the tag has moved."""
    for image in references:
        if not host_id(image):
            print(f'Pulling {image} for DinD.', file=sys.stderr, flush=True)
            subprocess.run(['docker', 'pull', '--quiet', image], stdout=subprocess.DEVNULL)


def in_dind(dind, script, *arguments, data=None, check=True):
    return docker('exec', '-i', '--user', 'rootless', dind, 'sh', '-c', DIND_DOCKER + script, 'sh', *arguments,
                  data=data, check=check)


def load(dind, references):
    """Copy each image into DinD unless it is there already; return the failures."""
    record = in_dind(dind, f'cat {RECORD} 2>/dev/null || echo {{}}').stdout
    try:
        loaded = json.loads(record)
    except ValueError:
        loaded = {}
    failures = []
    for image in references:
        identity = host_id(image)
        if not identity:
            failures.append(f'{image}: not on the host; docker pull failed.')
            continue
        present = in_dind(dind, 'docker image inspect "$1" >/dev/null 2>&1', image, check=False).returncode == 0
        if loaded.get(image) == identity and present:
            continue
        save = subprocess.Popen(['docker', 'save', image], stdout=subprocess.PIPE)
        result = subprocess.run(['docker', 'exec', '-i', '--user', 'rootless', dind, 'sh', '-c',
                                 DIND_DOCKER + 'docker load -q'], stdin=save.stdout, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        save.stdout.close()
        if save.wait() or result.returncode:
            failures.append(f'{image}: loading into DinD failed. {result.stderr.strip()}')
            continue
        loaded[image] = identity
        log('loaded', image)
    in_dind(dind, f'cat > {RECORD}', data=json.dumps(loaded))
    return failures


def finish(agent, content, failures=None):
    """Tell the agent's postStartCommand that loading has started, or is over and what failed."""
    name = marker(content) + ('.started' if failures is None else '')
    docker('exec', '-i', '--user', 'node', agent, 'sh', '-c', 'cat > "$0/$1"',
           MARKERS, name, data=''.join(line + '\n' for line in failures or []))


def wait_and_load(content):
    # Start only once the agent runs: DinD is found through the agent's Compose
    # project, and `up` may still be replacing an old agent. Then mark again
    # whenever the agent changes, as `up` may recreate it.
    references = images(content)
    failures = None
    marked = started = loaded_dind = None
    for _ in host_runtime.poll(WAIT_SECONDS):
        try:
            running = host_runtime.containers(ROOT)
            agent = running.get('agent')
            if agent and agent != started:
                finish(agent, content)
                started = agent
            if agent:
                dind = running.get('dind')
                if dind and dind != loaded_dind and in_dind(dind, 'docker info >/dev/null 2>&1', check=False).returncode == 0:
                    failures = load(dind, references)
                    loaded_dind = dind
                    marked = None
                    for failure in failures:
                        log(failure)
                # Keep watching for the whole startup window. An old agent may
                # still run during a build taking more than a minute, before
                # Compose replaces it and discards its tmpfs markers.
                if dind and dind == loaded_dind and failures is not None and agent != marked:
                    finish(agent, content, failures)
                    marked = agent
        except subprocess.CalledProcessError:
            pass  # Not ready yet.
    if failures is None:
        log(f'DinD did not start within {WAIT_SECONDS}s.')


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else None
    if mode == '--wait':
        return wait_and_load(sys.stdin.buffer.read())
    content = listed()
    if not images(content):
        return
    pull(images(content))
    if mode == '--background':
        host_runtime.background(__file__, '--wait', content, LOG)
        return
    running = host_runtime.containers(ROOT)
    dind = running.get('dind')
    if not dind:
        sys.exit('error: no running environment for this workspace. Run `devcontainer up`, then retry.')
    failures = load(dind, images(content))
    finish(running.get('agent'), content, failures)
    if failures:
        sys.exit('\n'.join(failures))


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
