"""Host-side container discovery and startup watcher utilities."""
import json
import subprocess
import sys
import time


def containers(root):
    """One snapshot of the running Compose services for this workspace.

    Only a unique agent selects the project. Ambiguous services are left out;
    never copy configuration or credentials into an arbitrarily chosen one.
    """
    result = subprocess.run(
        ['docker', 'ps', '--filter', 'label=com.docker.compose.project', '--format',
         '{"id":{{json .ID}},"workspace":{{json (.Label "devcontainer.local_folder")}},'
         '"project":{{json (.Label "com.docker.compose.project")}},'
         '"service":{{json (.Label "com.docker.compose.service")}}}'], capture_output=True, text=True, check=True)
    rows = [json.loads(line) for line in result.stdout.splitlines() if line]
    agents = [row for row in rows if row['workspace'] == str(root) and row['service'] == 'agent']
    if len(agents) != 1:
        return {}
    project = agents[0]['project']
    found = {}
    for row in rows:
        if row['project'] == project:
            found.setdefault(row['service'], []).append(row['id'])
    return {service: ids[0] for service, ids in found.items() if len(ids) == 1}


def poll(seconds, interval=5):
    """Watch through the build window, including recreation after early success."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        yield
        time.sleep(interval)


def log(*message):
    print(time.strftime('%F %T'), *message, flush=True)


def background(script, mode, data, path):
    """Pass a snapshot on stdin to a detached worker, with one append-only log."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as output:
        child = subprocess.Popen([sys.executable, str(script), mode], stdin=subprocess.PIPE,
                                 stdout=output, stderr=output, start_new_session=True)
    with child.stdin:
        child.stdin.write(data)
