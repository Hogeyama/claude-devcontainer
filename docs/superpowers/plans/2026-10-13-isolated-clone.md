# Isolated Clone with a Shared Git Remote Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the host's working tree and Git state outside the agent's reach, automatically initialize an independent clone on first startup, and exchange commits through an ordinary shared Git remote.

**Architecture:** The host writes a sanitized HTTPS origin URL and its exact HEAD to generated metadata. The container's post-start gate initializes a repository in a dedicated volume, fetches the remote, and checks out that commit as node. Subsequent starts preserve the clone without fetching or resetting it. Humans fetch agent branches, review two fixed commit IDs, and merge the reviewed ID with normal Git commands.

**Tech Stack:** Python 3.11 standard library, POSIX sh, Git, Docker Compose, Dev Containers CLI, existing inspecting proxy.

**Spec:** `docs/superpowers/specs/2026-10-12-isolated-clone-design.md`

**Status:** Implemented on 2026-10-13; verification results are recorded below. The user authorized one consolidated implementation commit after review. Checklist completion refers to implementation and checks; the example per-task commit steps were not executed. Gradle's existing CoW configuration is preserved, but a real Gradle project build was not run in this environment.

## Global Constraints

- Do not bind-mount the host's working tree or `.git`. Preserve the existing Gradle CoW mount; writes must stay in its upper layer. Use a CoW volume for root `node_modules`; additional monorepo mounts use the same pattern.
- Use standard Git remotes. Do not implement `cdc`, a remote helper, or a bundle transport.
- Automatically initialize the clone at startup; do not require a manual clone command.
- Before first startup, the host's HEAD must be reachable from a branch on the shared remote.
- Run Git in the container as node after capabilities have been dropped. Never run Git in an agent-controlled repository as root.
- Keep credentials in the existing proxy injection path. Generated metadata contains only a sanitized URL and a commit ID.
- Use `/run/host/generated` and `/run/host/nix-env` for trusted host-generated inputs, both read-only.
- Never fetch, checkout, reset, or remove files in a completed clone during startup. Preserve uncommitted work and local branches across recreation.
- Clone initialization may retry a fetch at most three times, with a 30-second timeout per attempt and a two-second delay between attempts. Failure must fail the startup gate.
- Retain existing network isolation, sumi, DinD, nix, and Claude session persistence.
- Follow existing Python `unittest` conventions in this repository. Tests use temporary repositories and synthetic credentials; Docker tests use unique project names and cleanup registrations.
- This plan changes source/configuration only when executed. Writing this plan does not authorize pushing to a real remote or deleting a user's volumes.

## File Map

| File | Responsibility |
| --- | --- |
| `.devcontainer/claude/scripts/workspace-source.py` (new) | Derive sanitized remote metadata on the host |
| `.devcontainer/claude/agent/workspace_mounts.py` (new) | Discover dependency mounts and prepare their ancestors without following symlinks |
| `.devcontainer/claude/agent/clone-workspace.py` (new) | Initialize the clone as node; preserve completed clones |
| `tests/workspace_clone_test.py` (new) | Real local Git tests for metadata, initialization, recovery, and review |
| `.devcontainer/claude/scripts/initialize.sh` | Generate metadata and create host output directories |
| `.devcontainer/claude/compose.yaml` | Independent workspace/cache volumes and trusted input mounts |
| `.devcontainer/claude/agent/Dockerfile` | Install initializer and give node ownership of initial volume contents |
| `.devcontainer/claude/agent/wait-ready.sh`, `bash-env.sh` | Startup gate and trusted generated paths |
| `.devcontainer/compose.yaml`, `devcontainer.json`, `claude.toml` | Adapt the repository's demo configuration |
| `tests/devcontainer_integration_test.py`, `agent_shell_integration_test.py` | Container isolation, restart, trusted paths, real automatic startup |
| `tests/fixtures/git_server.py` (new) | Local HTTPS Git smart HTTP upstream for Docker tests |
| `tests/fixtures/proxy_addon.py` | Route the Git fixture's accepted requests to the test upstream |
| `.devcontainer/compose.yaml` | Preserve Gradle CoW and define node_modules CoW mounts |
| `README.md`, `docs/usage.md`, `docs/network-policy.md`, `.devcontainer/claude/agent/skills/claude-devcontainer/SKILL.md` | Ordinary Git workflow, prerequisites, and trust boundary |

## Verification Commands

Run from the repository root. The Docker suites are opt-in; a skipped Docker suite is not evidence that automatic clone works.

```sh
python3 -m unittest discover -s tests -p '*_test.py'
python3 -m unittest discover -s .devcontainer/claude/proxy -p '*_test.py'
```

For Docker suites, generate test-only `.generated/proxy.json` from the template (without reading the developer's local settings), pass that directory as `workspace-config`, and build all three images. Use the existing fixture credentials, never real credentials. The exact isolated build commands are in Task 3.

---

## Task 1: Define and test automatic remote initialization

**Files:** Create `workspace-source.py`, `clone-workspace.py`, and `tests/workspace_clone_test.py` at the paths above.

**Interfaces:**

- `workspace_source.write(root: Path) -> dict[str, str]`: write `.devcontainer/.generated/workspace.json` atomically; return `{"url": ..., "commit": ...}`.
- `workspace_source.https_origin(value: str) -> str`: strip HTTPS userinfo, normalize the two supported GitHub SSH forms, reject unsupported transports and query/fragment URLs.
- `clone_workspace.clone(source: Path, workspace: Path) -> None`: perform first initialization or resume its recorded inputs; return without network access for a completed repository.
- CLI defaults: source `/run/host/generated/workspace.json`, workspace `/workspace`. Both scripts report errors without printing unsanitized origin values.

- [x] **Step 1: Write real Git tests before the implementation.**

Create this test module. A Git `url.*.insteadOf` rule maps the synthetic HTTPS URL to a temporary bare repository; the application still sees HTTPS and runs real Git. This checks repository semantics without implementing a fake Git command or contacting a live service.

```python
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


source = load('workspace_source', '.devcontainer/claude/scripts/workspace-source.py')
clone = load('clone_workspace', '.devcontainer/claude/agent/clone-workspace.py')


class WorkspaceCloneTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.host, self.remote, self.agent = [self.root / n for n in ('host', 'remote.git', 'agent')]
        config = self.root / 'gitconfig'
        config.write_text('[user]\n name = fixture\n email = fixture@example.com\n'
                          '[maintenance]\n auto = false\n'
                          f'[url "{self.remote}"]\n insteadOf = https://github.com/example/repo.git\n')
        environment = patch.dict(os.environ, {
            'GIT_CONFIG_GLOBAL': str(config), 'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_TERMINAL_PROMPT': '0',
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.host.mkdir()
        self.git(self.host, 'init', '-b', 'main')
        self.git(self.host, 'init', '--bare', str(self.remote))
        (self.host / 'package.json').write_text('{"scripts":{"test":"echo safe"}}\n')
        self.commit('initial')
        self.git(self.host, 'remote', 'add', 'origin', 'https://github.com/example/repo.git')
        self.git(self.host, 'push', 'origin', 'main')
        self.start = self.git(self.host, 'rev-parse', 'HEAD')
        self.metadata = self.host / '.devcontainer/.generated/workspace.json'
        source.write(self.host)

    def git(self, directory, *args):
        return subprocess.run(['git', '-C', str(directory), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, message):
        self.git(self.host, 'add', 'package.json')
        self.git(self.host, 'commit', '-m', message)

    def test_url_normalization(self):
        for value in ('https://user:secret@github.com/example/repo.git',
                      'git@github.com:example/repo.git',
                      'ssh://git@github.com/example/repo.git'):
            self.assertEqual(source.https_origin(value), 'https://github.com/example/repo.git')
        for value in ('/tmp/repo', 'file:///tmp/repo', 'ssh://git@example.com/repo',
                      'https://github.com/repo?token=secret', 'https://github.com/repo#secret'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                source.https_origin(value)

    def test_first_start_uses_exact_host_commit(self):
        (self.host / 'package.json').write_text('later\n')
        self.commit('remote advances after metadata was written')
        self.git(self.host, 'push', 'origin', 'main')
        clone.clone(self.metadata, self.agent)
        self.assertEqual(self.git(self.agent, 'rev-parse', 'HEAD'), self.start)
        self.assertEqual(self.git(self.agent, 'config', '--get', 'remote.origin.url'),
                         'https://github.com/example/repo.git')
        self.assertFalse((self.agent / '.git/claude-bootstrap.json').exists())

    def test_completed_clone_survives_new_metadata_and_unavailable_remote(self):
        clone.clone(self.metadata, self.agent)
        self.git(self.agent, 'switch', '-c', 'agent/task')
        (self.agent / 'package.json').write_text('unfinished\n')
        (self.agent / 'untracked').write_text('keep\n')
        self.metadata.write_text('{}')  # Must not be read on an ordinary restart.
        self.remote.rename(self.root / 'offline.git')
        clone.clone(self.metadata, self.agent)
        self.assertEqual(self.git(self.agent, 'branch', '--show-current'), 'agent/task')
        self.assertEqual((self.agent / 'package.json').read_text(), 'unfinished\n')
        self.assertEqual((self.agent / 'untracked').read_text(), 'keep\n')

    def test_unpublished_commit_can_resume_after_push(self):
        (self.host / 'package.json').write_text('not yet pushed\n')
        self.commit('local only')
        source.write(self.host)
        with self.assertRaisesRegex(ValueError, 'Push'):
            clone.clone(self.metadata, self.agent)
        self.assertTrue((self.agent / '.git/claude-bootstrap.json').exists())
        self.git(self.host, 'push', 'origin', 'main')
        clone.clone(self.metadata, self.agent)
        self.assertEqual(self.git(self.agent, 'rev-parse', 'HEAD'),
                         self.git(self.host, 'rev-parse', 'HEAD'))

    def test_nonempty_workspace_is_preserved(self):
        self.agent.mkdir()
        (self.agent / 'keep').write_text('data')
        with self.assertRaises(ValueError):
            clone.clone(self.metadata, self.agent)
        self.assertEqual((self.agent / 'keep').read_text(), 'data')

    def test_index_flags_do_not_cross_remote_and_review_id_is_stable(self):
        clone.clone(self.metadata, self.agent)
        self.git(self.agent, 'switch', '-c', 'agent/task')
        (self.agent / 'package.json').write_text('changed\n')
        self.git(self.agent, 'add', 'package.json')
        self.git(self.agent, 'commit', '-m', 'candidate')
        self.git(self.agent, 'update-index', '--assume-unchanged', 'package.json')
        (self.agent / 'package.json').write_text('concealed uncommitted change\n')
        self.assertEqual(self.git(self.agent, 'diff'), '')
        self.git(self.agent, 'push', 'origin', 'HEAD:refs/heads/agent/task')
        self.git(self.host, 'fetch', '--no-tags', 'origin', 'refs/heads/agent/task')
        candidate = self.git(self.host, 'rev-parse', 'FETCH_HEAD')
        self.assertIn('+changed', self.git(self.host, 'diff', self.start, candidate))
        self.assertTrue(self.git(self.host, 'ls-files', '-v', 'package.json').startswith('H '))
        self.git(self.agent, 'update-index', '--no-assume-unchanged', 'package.json')
        self.git(self.agent, 'add', 'package.json')
        self.git(self.agent, 'commit', '-m', 'later')
        self.git(self.agent, 'push', 'origin', 'HEAD:refs/heads/agent/task')
        self.git(self.host, 'fetch', 'origin')
        self.git(self.host, 'merge', '--ff-only', candidate)
        self.assertEqual(self.git(self.host, 'rev-parse', 'HEAD'), candidate)
        self.assertEqual((self.host / 'package.json').read_text(), 'changed\n')
```

Run `python3 -m unittest discover -s tests -p 'workspace_clone_test.py'`. Expect import failure because the two scripts do not exist yet.

- [x] **Step 2: Write the host metadata generator.**

`workspace-source.py`:

```python
#!/usr/bin/env python3
"""Write credential-free inputs for the agent's first remote clone."""
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[3]


def https_origin(value):
    if value.startswith('git@github.com:'):
        value = 'https://github.com/' + value.removeprefix('git@github.com:')
    if value.startswith('ssh://git@github.com/'):
        value = 'https://github.com/' + value.removeprefix('ssh://git@github.com/')
    url = urlsplit(value)
    if (url.scheme != 'https' or not url.hostname or not url.path.strip('/')
            or url.query or url.fragment or any(c.isspace() for c in value)):
        raise ValueError('Set origin to an HTTPS repository URL without a query or fragment.')
    return urlunsplit(('https', url.netloc.rsplit('@', 1)[-1], url.path, '', ''))


def write(root=ROOT):
    root = Path(root)
    def git(*args):
        return subprocess.run(['git', '-C', str(root), *args], check=True,
                              capture_output=True, text=True).stdout.strip()
    try:
        # Read the configured URL, not a URL rewritten through insteadOf.
        url = https_origin(git('config', '--get', 'remote.origin.url'))
        commit = git('rev-parse', '--verify', 'HEAD^{commit}')
    except subprocess.CalledProcessError:
        raise ValueError('A committed HEAD and an origin remote are required.') from None
    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', commit):
        raise ValueError('HEAD did not resolve to a full commit ID.')
    data = {'url': url, 'commit': commit}
    directory = root / '.devcontainer/.generated'
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / 'workspace.json.tmp'
    temporary.write_text(json.dumps(data) + '\n')
    temporary.replace(directory / 'workspace.json')
    return data


if __name__ == '__main__':
    try:
        write()
    except ValueError as error:
        sys.exit(f'error: {error}')
```

- [x] **Step 3: Write the container initializer.**

`clone-workspace.py`:

```python
#!/usr/bin/env python3
"""Initialize an independent clone once, without replacing user work."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit


def inputs(path):
    data = json.loads(Path(path).read_text())
    url = urlsplit(data['url'])
    if (url.scheme != 'https' or not url.hostname or url.username is not None
            or url.password is not None or url.query or url.fragment
            or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', data['commit'])):
        raise ValueError('Invalid workspace clone metadata.')
    return data


def clone(source, workspace):
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    marker = workspace / '.git/claude-bootstrap.json'
    environment = dict(os.environ, GIT_TERMINAL_PROMPT='0')

    def git(*args, check=True, timeout=30):
        return subprocess.run(['git', '-C', str(workspace), *args],
                              env=environment, check=check, timeout=timeout,
                              capture_output=True, text=True)

    if marker.is_file():
        data = inputs(marker)
    elif (workspace / '.git').is_dir():
        if git('rev-parse', '--verify', 'HEAD^{commit}', check=False).returncode == 0:
            return
        raise ValueError('Incomplete repository without initialization metadata; inspect /workspace.')
    else:
        if any(workspace.iterdir()):
            raise ValueError('Workspace is not empty; inspect it before initializing a clone.')
        data = inputs(source)
        # Record intent before git init, so interruptions can be retried.
        marker.parent.mkdir()
        marker.write_text(json.dumps(data) + '\n')

    git('init')
    if git('remote', 'get-url', 'origin', check=False).returncode:
        git('remote', 'add', 'origin', data['url'])
    elif git('config', '--get', 'remote.origin.url').stdout.strip() != data['url']:
        raise ValueError('Origin changed during initialization; inspect /workspace before retrying.')
    for attempt in range(3):
        try:
            git('fetch', '--no-tags', 'origin')
            break
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if attempt == 2:
                raise ValueError('Could not fetch origin. Check proxy policy and credentials, '
                                 'then run wait-ready.sh as node again.') from None
            time.sleep(2)
    if git('cat-file', '-e', data['commit'] + '^{commit}', check=False).returncode:
        raise ValueError('Push the starting commit to a remote branch, then run wait-ready.sh again.')
    git('checkout', '--detach', data['commit'])
    marker.unlink()


if __name__ == '__main__':
    try:
        clone('/run/host/generated/workspace.json', '/workspace')
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        sys.exit(f'error: workspace initialization failed: {error}')
```

Do not add a forced checkout/reset or fallback to the remote's latest HEAD. The `git init` + remote + fetch sequence makes an independent clone while allowing a persistent initialization marker; it has no custom transfer protocol.

- [x] **Step 4: Extend error/recovery checks and run the suite.**

Add cases to `WorkspaceCloneTest` using the same real repositories:

| Case | Setup and assertion |
| --- | --- |
| Metadata has no secret | Put userinfo in host `remote.origin.url`; `source.write` output must not contain that userinfo |
| Missing origin / unborn HEAD | Remove origin, then separately create an empty repository; each must raise `ValueError` and not publish metadata |
| Interrupted initialization | Create `.git/claude-bootstrap.json` with valid inputs but no Git files; run `clone`; assert correct HEAD and marker removal |
| Checkout conflict | After a failed unpublished-commit attempt, create an untracked `package.json`; retry after push; Git must refuse to overwrite it and retain the marker |
| Fetch unavailable | Rename the temporary bare remote; patch only `clone.time.sleep` to avoid a test delay; assert failure, marker retention, and successful retry after restoring the remote |
| Invalid metadata | Wrong scheme, credential-bearing URL, malformed commit ID; assert no checkout and no network access |

Run `python3 -m unittest discover -s tests -p 'workspace_clone_test.py'`; expect all tests to pass. Commit the two scripts and their tests with `feat: initialize isolated workspaces from a shared remote`.

---

## Task 2: Wire independent volumes into startup

**Files:** `.devcontainer/claude/{compose.yaml,scripts/initialize.sh,agent/Dockerfile,agent/wait-ready.sh,agent/bash-env.sh}`, `.devcontainer/{compose.yaml,devcontainer.json}`, `tests/agent_shell_integration_test.py`.

**Consumes:** Task 1's metadata and clone initializer.
**Produces:** `/workspace` owned by node, initialized before `devcontainer up` returns, alongside CoW dependency mounts.

- [x] **Step 1: Replace the core workspace mounts.**

Replace the host workspace, `.devcontainer`, and every `.git` bind mount in the agent's `volumes` with:

```yaml
      - workspace:/workspace
      - type: bind
        source: ../.generated
        target: /run/host/generated
        read_only: true
        bind: {create_host_path: false}
      - type: bind
        source: ../.nix-env
        target: /run/host/nix-env
        read_only: true
        bind: {create_host_path: false}
```

Keep the existing CA, socket, Claude session, and nix store mounts. Add `workspace:` to the top-level `volumes` map with no fixed global `name`. Preserve the workspace-owned Gradle CoW configuration; do not replace it with an empty cache volume.

- [x] **Step 2: Generate trusted startup inputs on the host.**

In `initialize.sh`, retain the root-repository check (worktree support is not part of this change). Remove `mkdir -p .git/hooks .git/modules` and its mount-specific comment. Immediately after successful `settings.py generate`, add:

```sh
python3 "$(dirname "$0")/workspace-source.py"
mkdir -p .devcontainer/.nix-env
```

Rewrite the nix comment to say it evaluates a commit in the host's repository. It must no longer claim the agent can move that repository's refs. Keep settings, network selection, credential injection, host configuration copy, and image loading in their current order.

- [x] **Step 3: Install the initializer and configure the startup gate.**

In `agent/Dockerfile`, keep the existing `/workspace` owner setup and add:

```dockerfile
COPY --chmod=755 agent/clone-workspace.py /usr/local/bin/clone-workspace.py
```

Remove `git config --system branch.autoSetupMerge false` and its comment: the clone's config is writable. Do not add recursive root `chown` or run Git from the root portion of the entrypoint.

In `wait-ready.sh`, immediately after the host configuration copy wait, add:

```sh
# The post-start gate runs as remoteUser=node, after entrypoint drops caps.
python3 /usr/local/bin/clone-workspace.py
```

Change its image list to `/run/host/generated/images.txt`. Change both nix paths in `bash-env.sh` to `/run/host/nix-env/env.rc`. Change the path in the shell test's generated `env.rc` and bind target to `/run/host/nix-env`; keep its existing Bash-mode matrix.

- [x] **Step 4: Preserve this repository's Gradle CoW override.**

Keep the `gradle-home` mount, its overlay driver options, and the Gradle directory creation in `.devcontainer/devcontainer.json`. Keep the comment prohibiting host Gradle activity while the overlay is mounted. Remove only the `.secrets` masking mount after the host workspace is no longer shared; sumi retains its separate mount. Implement the Node CoW mount in Task 4 before startup validation; remove the current read-only node_modules bind mount.

Keep `workspaceFolder: /workspace`, `remoteUser: node`, `postStartCommand: wait-ready.sh`, and `waitFor: postStartCommand` in both the demo and the template.

- [x] **Step 5: Check configuration and commit.**

Run both non-Docker unittest discovery commands and inspect `docker compose config` in Task 3's disposable environment. Assert there is one named volume at `/workspace`, no host source at `/workspace/.git`, and generated inputs are read-only. Commit with `feat: isolate agent files and caches in project volumes`. Complete the runtime verification in Task 3 before claiming this task works end to end.

---

## Task 3: Verify automatic clone through the real proxy and container boundary

**Files:** `tests/fixtures/git_server.py` (new), `tests/fixtures/proxy_addon.py`, `tests/devcontainer_integration_test.py`, `tests/agent_shell_integration_test.py`.

**Consumes:** Task 2's startup lifecycle and normal Git HTTPS requests.
**Produces:** A local smart HTTP fixture and regression coverage that exercises actual Git, proxy injection, and Docker mounts.

- [x] **Step 1: Add an HTTPS upstream with real git-http-backend.**

The existing `proxy_server.py` returns synthetic bytes for `/git-upload-pack`; it cannot serve a clone. Leave that response in place for existing proxy streaming tests. Add a separate server, run in a test-only container based on `AGENT_IMAGE` (which already has Git and Python). Run this server as node with no capabilities; it is an upstream fixture, not the agent entrypoint.

`tests/fixtures/git_server.py`:

```python
import http.server
import os
from pathlib import Path
import ssl
import subprocess
from urllib.parse import urlsplit


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def handle_git(self):
        if self.headers.get('Transfer-Encoding'):
            self.send_error(411, 'Fixture expects Content-Length')
            return
        size = int(self.headers.get('Content-Length', '0'))
        body = self.rfile.read(size)
        url = urlsplit(self.path)
        environment = dict(os.environ, GIT_PROJECT_ROOT='/repos', GIT_HTTP_EXPORT_ALL='1',
                           PATH_INFO=url.path, QUERY_STRING=url.query,
                           REQUEST_METHOD=self.command,
                           CONTENT_TYPE=self.headers.get('Content-Type', ''),
                           CONTENT_LENGTH=str(size), REMOTE_USER='fixture',
                           HTTP_GIT_PROTOCOL=self.headers.get('Git-Protocol', ''))
        backend = subprocess.check_output(['git', '--exec-path'], text=True).strip()
        result = subprocess.run([str(Path(backend) / 'git-http-backend')],
                                env=environment, input=body, capture_output=True, check=True)
        headers, payload = result.stdout.split(b'\r\n\r\n', 1)
        pairs = [line.decode().split(':', 1) for line in headers.split(b'\r\n')]
        status = next((int(v.strip().split()[0]) for k, v in pairs if k.lower() == 'status'), 200)
        self.send_response(status)
        for key, value in pairs:
            if key.lower() not in ('status', 'content-length', 'connection'):
                self.send_header(key, value.strip())
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = handle_git


server = http.server.ThreadingHTTPServer(('0.0.0.0', 9443), Handler)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain('/fixtures/server.pem', '/fixtures/server.key')
server.socket = context.wrap_socket(server.socket, server_side=True)
server.serve_forever()
```

Use only small fixture commits so pushes have Content-Length. The fixture's limitation must not become a production Git restriction.

In `FixturePolicy.server_connect`, retain the production policy call and reject behavior. For accepted connections with `data.server.address[0] == 'github.com'` and `GIT_FIXTURE_HOST` set, preserve SNI as `github.com` and set the upstream address to `(os.environ['GIT_FIXTURE_HOST'], 9443)`. Otherwise retain `('127.0.0.1', 9443)`. Add `import os`. The complete replacement method is:

```python
    def server_connect(self, data):
        super().server_connect(data)
        if data.server.error is None:
            hostname = data.server.address[0]
            data.server.sni = hostname
            if hostname == 'github.com' and os.environ.get('GIT_FIXTURE_HOST'):
                data.server.address = (os.environ['GIT_FIXTURE_HOST'], 9443)
            else:
                data.server.address = ('127.0.0.1', 9443)
```

The existing TLS certificate already covers `github.com`.

- [x] **Step 2: Prepare the isolated remote before devcontainer up.**

Extend `DevcontainerIntegrationTest.start_environment` after committing the fixture workspace:

```python
remote_root = cls.directory / 'repos'
remote_root.mkdir()
remote = remote_root / 'example/repo.git'
remote.parent.mkdir()
subprocess.run(['git', 'clone', '--bare', str(cls.workspace), str(remote)], check=True)
subprocess.run(['git', '-C', str(remote), 'config', 'http.receivepack', 'true'], check=True)
subprocess.run([*git, 'remote', 'add', 'origin', 'https://github.com/example/repo.git'], check=True)
cls.initial_commit = subprocess.check_output([*git, 'rev-parse', 'HEAD'], text=True).strip()
```

The fixture repository must be writable by `AGENT_UID:AGENT_GID`. Use a test-only setup container to chown only the temporary `/repos` mount when those IDs differ from the test runner; never chown a developer repository.

Add a `git-fixture` service in the disposable Compose override, on `proxy-external` only:

```python
'git-fixture': {
    'image': AGENT_IMAGE,
    'user': f'{AGENT_UID}:{AGENT_GID}',
    'cap_drop': ['ALL'],
    'entrypoint': ['python3', '/fixtures/git_server.py'],
    'volumes': [str(remote_root) + ':/repos', str(cls.directory / 'upstream') + ':/fixtures:ro'],
    'networks': ['proxy-external'],
}
```

Create `upstream/` with only `git_server.py`, `server.pem`, and `server.key`. Set the proxy's `GIT_FIXTURE_HOST=git-fixture`, and add a service-start dependency on it. Its TLS certificate is the same synthetic one trusted by the existing proxy fixture.

Before `up`, create the synthetic credentials config with `GITHUB_TOKEN=synthetic-managed-token` and `ANTHROPIC_API_KEY=synthetic-managed-token`. This lets the real background injector satisfy the clone; injecting only after `up` would deadlock the new startup gate. Keep the delayed entrypoint so the gate is exercised.

Narrow the agent's `/startup-fixture` bind mount to a directory containing only `entrypoint.sh`. The existing whole-fixture-directory mount would expose the host test workspace and bare remote, invalidating the isolation test. The agent must have no path to either repository except HTTPS through the proxy.

- [x] **Step 3: Replace the shared-workspace assertions.**

Replace `test_devcontainer_directory_is_read_only` with a test that edits the clone's `.devcontainer` while asserting the corresponding host file remains byte-for-byte unchanged. Replace `test_git_settings_are_read_only_but_commits_work` with independent config/hook/commit checks:

```python
def test_agent_git_and_config_changes_do_not_reach_host(self):
    host_head = subprocess.check_output(['git', '-C', str(self.workspace), 'rev-parse', 'HEAD'], text=True)
    host_config = (self.workspace / '.git/config').read_bytes()
    host_devcontainer = (self.workspace / '.devcontainer/devcontainer.json').read_bytes()
    self.exec('sh', '-ec', '''
        cd /workspace
        git switch -c agent/isolation
        git config isolation.probe agent-only
        printf '#!/bin/sh\nexit 0\n' > .git/hooks/pre-commit
        chmod +x .git/hooks/pre-commit
        printf '\n' >> .devcontainer/devcontainer.json
        git add .devcontainer/devcontainer.json
        git -c user.name=fixture -c user.email=fixture@example.com commit -m isolation
        git push -u origin agent/isolation
    ''')
    self.assertEqual((self.workspace / '.git/config').read_bytes(), host_config)
    self.assertEqual((self.workspace / '.devcontainer/devcontainer.json').read_bytes(), host_devcontainer)
    self.assertEqual(subprocess.check_output(
        ['git', '-C', str(self.workspace), 'rev-parse', 'HEAD'], text=True), host_head)
    self.exec('git', 'switch', '--detach', self.initial_commit)
```

Also check these observable outcomes in the existing integration suite:

| Behavior | Assertion |
| --- | --- |
| Automatic first clone | Immediately after `up`, `/workspace` HEAD equals `initial_commit`; origin is the HTTPS fixture URL |
| Trusted host inputs | Writes to `/run/host/generated/workspace.json` and `images.txt` fail; clone-local `.devcontainer/.generated` cannot replace those inputs |
| Restart/recreation | Write an uncommitted file and cache markers; recreate only the agent with its named volumes retained; all markers and the branch survive |
| Existing clone needs no remote | Stop `git-fixture`, run the clone initializer again, and verify success with dirty files intact; restart fixture in cleanup |
| Credentials | Bare remote receives the pushed branch; proxy still strips agent-supplied auth and injects synthetic auth (existing proxy suite) |
| Isolation | Docker inspect shows no host source mounted as `/workspace` or `.git`; the only direct repository bind mounts are generated inputs; node_modules uses CoW |

Register cleanup before starting each service. Keep the existing firewall, secret masking, offline DinD, Testcontainers, and Claude Bash tests; do not substitute a volume preloaded from the host for automatic clone.

- [x] **Step 4: Build with isolated settings and run the suites.**

```sh
build_fixture=$(mktemp -d)
mkdir -p "$build_fixture/.devcontainer"
cp .devcontainer/claude/template/claude.toml "$build_fixture/.devcontainer/claude.toml"
python3 - "$build_fixture" <<'PY'
import importlib.util
from pathlib import Path
import sys
spec = importlib.util.spec_from_file_location('settings', '.devcontainer/claude/scripts/settings.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
root = Path(sys.argv[1])
out = root / '.devcontainer/.generated'
out.mkdir()
(out / 'proxy.json').write_text(module.proxy_json(module.load(root)))
PY
```

The existing `settings.load(root)` and `settings.proxy_json(settings)` helpers read only the disposable workspace's settings. The fixture must not execute the developer's credential configuration.

```sh
docker build -t claude-devcontainer-agent:task2 \
  --build-arg AGENT_UID="${CLAUDE_DEVCONTAINER_UID:-1000}" \
  --build-arg AGENT_GID="${CLAUDE_DEVCONTAINER_GID:-1000}" \
  --build-context workspace-config="$build_fixture/.devcontainer" \
  -f .devcontainer/claude/agent/Dockerfile .devcontainer/claude
docker build -t claude-devcontainer-sumi:task2 --target sumi-serve \
  --build-context workspace-config="$build_fixture/.devcontainer" \
  -f .devcontainer/claude/agent/Dockerfile .devcontainer/claude
docker build -t claude-devcontainer-proxy:task1 \
  --build-context workspace-config="$build_fixture/.devcontainer" .devcontainer/claude/proxy
RUN_DOCKER_TESTS=1 python3 -m unittest discover -s tests -p 'proxy_integration_test.py'
RUN_DOCKER_TESTS=1 python3 -m unittest discover -s tests -p 'devcontainer_integration_test.py'
RUN_DOCKER_TESTS=1 python3 -m unittest discover -s tests -p 'agent_shell_integration_test.py'
```

Expect all suites to pass without real service credentials. Remove only the temporary build fixture after the build. Commit with `test: verify remote clone startup and host isolation`.

---

## Task 4: Reuse host node_modules through CoW; preserve Gradle CoW

**Files:** `.devcontainer/claude/scripts/settings.py`, `.devcontainer/claude/scripts/initialize.sh`, `.devcontainer/claude/compose.yaml`, `.devcontainer/compose.yaml`, `.devcontainer/claude/agent/clone-workspace.py`, `tests/workspace_clone_test.py`, `tests/settings_test.py`, and a new Docker integration test for dependency overlays.

**Interfaces:** Host-generated settings supply absolute lower/upper/work paths for the root node_modules overlay. The existing core Compose include receives `.generated/settings.env`; put core volume options there rather than relying on the caller's cwd or expecting those variables in the workspace's outer Compose file. Reuse existing settings generation and validation patterns. Place upper/work under the host state directory, keyed by canonical repository path, outside Docker build contexts. Reject paths that cannot safely be represented in overlay mount options. No new package-registry permission is required.

- [x] **Step 1: Add tests for CoW isolation and lifecycle.**

Use only disposable host directories and uniquely named Docker volumes. Prepare a lower tree with a module file, a file to delete, and a hardlink to a separate synthetic store file. Mount overlay as the configured node UID. Modify the module, delete the second file, modify the hardlink, and create a new file. Assert the lower tree and synthetic store remain unchanged, the overlay sees the changes, and the changes survive container recreation. Exercise a pnpm-style relative symlink into `node_modules/.pnpm`. Clean up only test-created volumes/directories. Register cleanup before each creation.

- [x] **Step 2: Generate paths and mount the overlay.**

Create an empty host `node_modules` if absent, and repository-specific upper/work directories. Keep upper and work on the same filesystem. Emit their absolute paths through `.generated/settings.env` using the existing quoting/validation code. Use a Docker local volume with `type: overlay`, `device: overlay`, and `o: lowerdir=...,upperdir=...,workdir=...`. Mount it at `/workspace/node_modules`. Ensure node can write the upper view without recursive root traversal of agent-owned data. Remove the demo's `../node_modules:/workspace/node_modules:ro` override. Keep all existing Gradle CoW options and its directory setup unchanged.

- [x] **Step 3: Allow dependency mountpoints during first clone.**

Update Task 1's sample initializer: the strict `any(workspace.iterdir())` check is superseded. Permit actual node_modules mountpoints and ancestor directories containing only such mounts. Do not accept ordinary directories named node_modules solely by name, symlinks to external locations, or unrelated existing files. Inspect the fetched commit tree before checkout and reject tracked paths at, below, or conflicting with dependency mounts. Do not recurse through dependency contents or modify them during clone. Add tests for root and nested mountpoints, unrelated-file refusal, tracked dependency path refusal, and preservation of dependency contents.

- [x] **Step 4: Validate refresh without losing clone work.**

Document an explicit host-side sequence: stop/remove the workspace containers so overlays are unmounted; preserve any needed agent-only dependency edits; update host dependencies; remove/recreate only that repository's node_modules upper/work state; start again. If a reset helper is added, it must refuse mounted/in-use overlays and unrelated paths. Never reset dependencies automatically at ordinary startup, never use a blanket `down --volumes`, and never delete the workspace clone or Gradle state. Demonstrate the sequence on disposable directories with both modified files and whiteouts, ensuring refreshed dependencies appear and unrelated clone edits survive.

- [ ] **Step 5: Run real dependency and isolation checks.**

Node, npm, Testcontainers and Docker isolation checks passed. Gradle project execution remains unverified; no Gradle settings or registry permissions were changed.

Keep Gradle offline through its existing CoW mount (`./gradlew --offline jar test` with host-populated fixtures). For Node, install fixture dependencies on the host before starting the overlay and run `npm test` / `pnpm test` in the container; do not run `npm ci` or require online installs. Testcontainers continues using DinD. Run the non-Docker suites plus the CoW and startup Docker suites. Record any actual environment limitation rather than claiming skipped tests passed.

Do not add package registry routes, a new Gradle cache volume, or `JAVA_TOOL_OPTIONS` as part of clone isolation. Support root node_modules by default; document explicit additional mounts for monorepos. Yarn PnP/global external stores and incompatible native addons are outside the automatic reuse guarantee. Commit only if the supervising task authorizes commits.

---

## Task 5: Document the ordinary Git workflow and remaining startup requirements

**Files:** `README.md`, `docs/usage.md`, `docs/network-policy.md`, `.devcontainer/claude/agent/skills/claude-devcontainer/SKILL.md`, `.devcontainer/claude/template/claude.toml`, `.devcontainer/agent-setup.sh`, `.devcontainer/claude.toml`.

**Consumes:** Tasks 1–4. No new commands or runtime behavior.

- [x] **Step 1: Update README prerequisites and quickstart.**

Replace the shared `.git` warning with:

> agentは専用volumeのcloneで作業します。ホストの作業ツリーや`.git`は共有しません。初回起動前に、ホストのHEADを共通リモートのブランチへpushしてください。起動処理がそのコミットを自動で取得します。未コミットの変更や、ホストにしかないコミットは入りません。

After the quickstart's setup commit, include `git push origin HEAD` before container startup. Explain that origin must be HTTPS or one of the supported GitHub SSH forms, and that clone uses HTTPS through the proxy regardless of the host's transport. GitHub PAT and proxy rules must permit reading the starting repository and writing the agent's task branches.

- [x] **Step 2: Add review and return-work instructions.**

Copy the spec's exact `fetch --no-tags` → capture `agent_commit` / `base_commit` → diff → HEAD guard → `merge --ff-only` sequence and the reviewed-ID worktree alternative. Explain rebase/re-review after divergence and ordinary push/fetch in the reverse direction. Do not replace those steps with `git pull` or a wrapper.

Change the shutdown instructions: named volumes retain unpublished and uncommitted agent work; `down --volumes` deletes it. Before deliberate deletion, commit and push required work and verify the remote has it.

- [x] **Step 3: Update the security and usage documents.**

In `docs/network-policy.md`, replace the old read-only workspace/config table and the entire prompt-warning subsection with the independent-clone model from the spec. Remove the starship and Powerlevel10k checks: they rely on a diff the agent could hide. Retain the host-configuration one-way-copy and proxy credential explanations. Repoint links to the removed subsection to `ホストが実行する設定の保護`.

In `docs/usage.md`, describe VS Code as showing the agent clone. With automatic clone, private repositories need credentials injected during startup; the existing instruction to inject only after a successful `up` must be replaced. Configure the host credential source before `up`; if injection was unavailable, inject into the running proxy and retry `wait-ready.sh` as node. Keep the existing dedicated VS Code profile and prohibition on forwarded host credential sockets.

For nix and image-loading warnings, explain that host-generated configuration comes from the host's files, and agent changes affect it only after review and integration. Document `/run/host/generated` and `/run/host/nix-env`.

- [x] **Step 4: Update the managed agent skill and demo comments.**

Replace `## Files and Git` with:

```markdown
## Your clone

- `/workspace` is your independent clone. The host's working tree and Git state are not mounted here.
- First startup checks out the host's published starting commit as detached HEAD. Create a task branch, such as `agent/task-name`, before committing work.
- Commit and push that branch to `origin` when asking the human to review or run it. Uncommitted changes and untracked files are not transferred.
- The human fetches and reviews a specific commit before merging or checking it out. A push alone does not mean the human trusts the change.
- To take the human's changes, fetch origin and rebase or merge the branch they name. Restarting the container does not update your clone.
- You may edit your `.git/config` and use `git push -u`. Your hooks and Git configuration remain inside this container.
- Changes to `.devcontainer/` take effect only after the human reviews and integrates them on the host and restarts the environment.
- When a task must run on the host, push the necessary commit and ask in chat for the command to be run after review. Results return in chat.
```

Preserve the existing sumi explanation and network rules. Retain the Gradle CoW/offline guidance and describe the node_modules CoW reuse and refresh lifecycle. Update the template's UID comment to describe the volume owner rather than host workspace write permission. Remove the obsolete comment about hiding the shared `.secrets`; preserve the Gradle overlay comments and describe the Node CoW mount. Explain that the committed sumi demo value is available in the clone, while real secret lists belong outside Git and remain mounted only in the sumi service. Remove the host `update-index --assume-unchanged` setup instruction.

- [x] **Step 5: Audit documentation and commit.**

```sh
rg -n 'cdc (push|fetch|merge|pull)|workspace.bundle|refs/remotes/agent/main' \
  README.md docs/usage.md docs/network-policy.md .devcontainer/claude/agent/skills
rg -n 'assume-unchanged|read-only|読み取り専用|overlay|/workspace/.devcontainer' \
  README.md docs/usage.md docs/network-policy.md .devcontainer/claude
```

The first search should have no matches. Inspect the second: remaining statements must refer to trusted input mounts, retained security mechanisms, or the motivation, never a shared writable host repository. Run both non-Docker suites and `git diff --check`. Commit with `docs: explain automatic clones and reviewed Git integration`.

## Completion Evidence

Record the test commands and actual results, including any skipped Docker or network-dependent checks. Do not claim the old plan's historical test counts. The completed change must demonstrate automatic first startup through the proxy, unchanged host files after agent edits, preserved work after recreation, and reviewed commit integration using standard Git only.


### Execution results (2026-10-13)

- `python3 -m unittest discover -s tests -p '*_test.py'`: 113 tests, OK; 25 opt-in Docker cases skipped in this run.
- Proxy unit tests in the pinned mitmproxy image, with the template policy and no network: 34 tests, OK.
- `RUN_DOCKER_TESTS=1 ... python3 -m unittest discover -s tests -p '*integration_test.py'`: 32 tests, OK, no skips, with pinned Testcontainers 12.1.0 fixture dependencies prepared on the host.
- After fixing ownership of nested dependency-mount ancestors: devcontainer integration suite 17 tests, OK, no skips. Startup runs Git only as node after dropping capabilities. The fixture includes a nested node_modules mount and a tracked file beside it.
- CoW test additionally refreshed only Node upper/work while unmounted: host dependencies became visible again, whiteouts were removed and the separate workspace volume retained its untracked work.
- `git diff --check` and shell syntax checks passed. Code review identified the nested mount ownership issue; the corrected descriptor-based, no-symlink preparation had no remaining important findings.
- Gradle's existing demo overlay and initializeCommand are retained. A real Gradle project build and VS Code GUI behavior were not executed.

Implementation adaptations: Node paths are computed by settings generation, while initialize.sh creates the directories; ordinary compose inspection does not create caches. Root prepares only the workspace and actual dependency-mount ancestor directories through held directory descriptors with O_NOFOLLOW, never recursively and never running Git. Git object format is selected from the starting commit ID. Temporary Docker test resources use separate names and are removed; no production container or real Git remote was changed.
