#!/usr/bin/env python3
"""Expose the running container's Claude over ACP, without host tool access.

Run this on the host. stdout is exclusively newline-delimited JSON-RPC.
Needs git.isolation = "shared" or "unsafe_shared": the agent's /workspace is then the host's
working tree, so the paths the client and the agent exchange name the same
files. The host owns this boundary: it lives in .devcontainer, which shared
mode mounts read-only for the agent.
"""
import argparse
import importlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import selectors
import signal
import subprocess
import sys
import time
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import layout  # noqa: E402

# The workspace root (scripts/layout.py).
ROOT = layout.workspace_root()
MAX_FRAME = 32 * 1024 * 1024
# The agent's own .git, a volume over the host's: the same path names
# different files on either side.
AGENT_GIT = '/workspace/.git'


def send(stream, message):
    stream.write((json.dumps(message, ensure_ascii=True, allow_nan=False) + '\n').encode())
    stream.flush()


def reject_json_constant(value):
    raise ValueError(f'Invalid JSON constant: {value}')


def error(message, code, detail):
    return {'jsonrpc': '2.0', 'id': message['id'],
            'error': {'code': code, 'message': detail}}


def validate_message(message):
    if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
        raise ValueError('Expected a JSON-RPC 2.0 object.')
    if 'id' in message and type(message['id']) not in (str, int):
        raise ValueError('ACP request IDs must be strings or integers.')
    # Clients disagree about mixed request/response objects. Reject them so
    # an allowed notification cannot smuggle a response past its own filter.
    if 'method' in message:
        if not isinstance(message['method'], str) or 'result' in message or 'error' in message:
            raise ValueError('Invalid ACP request or notification.')
        if not isinstance(message.get('params', {}), dict):
            raise ValueError('ACP params must be an object.')
    elif 'id' not in message or ('result' in message) == ('error' in message):
        raise ValueError('Invalid ACP response.')


def under(path, prefix):
    return path == prefix or path.startswith(prefix + '/')


def without_meta(value):
    # Extension metadata can instruct clients to start terminals or open URLs.
    # Only the standard ACP presentation/permission surface crosses back out.
    if isinstance(value, dict):
        return {k: without_meta(v) for k, v in value.items() if k != '_meta'}
    if isinstance(value, list):
        return [without_meta(v) for v in value]
    return value


class Bridge:
    def __init__(self, root, client, agent):
        self.roots = (str(Path(root).absolute()), str(Path(root).resolve()))
        # Where the agent's paths are shown; the shared mount's source.
        self.host_root = self.roots[1]
        self.client = client
        self.agent = agent
        self.pending = {}
        self.permissions = set()

    def container_path(self, value):
        if not isinstance(value, str) or not value.startswith('/'):
            raise ValueError('ACP cwd must be an absolute workspace path.')
        path = os.path.normpath(value)
        def mapped(candidate, prefixes):
            for prefix in prefixes:
                if candidate == prefix:
                    return '/workspace'
                if candidate.startswith(prefix + '/'):
                    return '/workspace' + candidate[len(prefix):]
            return None
        result = mapped(path, (*self.roots, '/workspace'))
        if result is None:
            # Only client-supplied paths reach here. The host editor may use a
            # symlink alias even when Docker's label and our root are canonical.
            # Never resolve paths received from the agent on the host.
            try:
                result = mapped(str(Path(value).resolve()), self.roots)
            except (OSError, RuntimeError):
                result = None
        if result is None:
            raise ValueError('ACP cwd must be inside this workspace.')
        if under(result, AGENT_GIT):
            raise ValueError("The host's .git is not the agent's; ACP paths cannot point into it.")
        return result

    def host_path(self, value):
        """The host's name for a path the agent sent, or the path unchanged.

        Prefix mapping only: the agent can plant symlinks in the shared tree,
        so its paths are never resolved on the host.
        """
        if not isinstance(value, str) or not value.startswith('/'):
            return value
        path = posixpath.normpath(value)
        if not under(path, '/workspace') or under(path, AGENT_GIT):
            return value
        return self.host_root + path[len('/workspace'):]

    def host_uri(self, uri):
        parsed = urlsplit(uri)
        if parsed.scheme != 'file' or parsed.netloc not in ('', 'localhost'):
            return uri
        path = unquote(parsed.path)
        mapped = self.host_path(path)
        return uri if mapped == path else PurePosixPath(mapped).as_uri()

    def host_paths(self, value):
        """Map the paths of tool locations, diffs, resources and sessions for the editor.

        The tool's raw input and output stay as the agent reported them.
        """
        if isinstance(value, list):
            return [self.host_paths(v) for v in value]
        if not isinstance(value, dict):
            return value
        result = {k: v if k in ('rawInput', 'rawOutput') else self.host_paths(v)
                  for k, v in value.items()}
        if isinstance(result.get('uri'), str):
            result['uri'] = self.host_uri(result['uri'])
        if isinstance(result.get('cwd'), str):
            result['cwd'] = self.host_path(result['cwd'])
        if result.get('type') == 'diff' and 'path' in result:
            result['path'] = self.host_path(result['path'])
        if isinstance(result.get('locations'), list):
            result['locations'] = [{**l, 'path': self.host_path(l['path'])} if isinstance(l, dict) and 'path' in l
                                   else l for l in result['locations']]
        return result

    def resource_paths(self, value):
        if isinstance(value, list):
            return [self.resource_paths(v) for v in value]
        if not isinstance(value, dict):
            return value
        result = {k: self.resource_paths(v) for k, v in value.items()}
        uri = result.get('uri')
        if isinstance(uri, str):
            parsed = urlsplit(uri)
            if parsed.scheme == 'file' and parsed.netloc in ('', 'localhost'):
                path = unquote(parsed.path)
                try:
                    result['uri'] = PurePosixPath(self.container_path(path)).as_uri()
                except ValueError:
                    pass  # A resource outside this workspace, or in .git, is unchanged.
        return result

    def from_client(self, message):
        # SDK options in _meta can also carry MCP servers and credentials.
        # Keep the boundary on standard ACP fields in both directions.
        message = without_meta(message)
        method = message.get('method')
        if method is None:
            if message.get('id') in self.permissions:
                self.permissions.remove(message['id'])
                send(self.agent, message)
            return
        params = message.get('params', {})
        try:
            params = dict(params)
            # Clients often attach their host MCP config automatically,
            # including env, headers and credentials in URLs or arguments.
            # Container MCP servers must be configured inside the container.
            if 'mcpServers' in params or method in ('session/new', 'session/load', 'session/resume', 'session/fork'):
                params['mcpServers'] = []
            if method == 'initialize':
                # No fs, terminal, auth terminal, URL elicitation, or extensions.
                # The working tree is shared, but the editor would read and
                # write it as the host user: past the agent's read-only
                # .devcontainer, hidden paths and own .git, and through any
                # symlink the agent plants. The agent edits on its own mounts;
                # the editor sees the result on disk.
                params['clientCapabilities'] = {}
                params.pop('_meta', None)
            if method == 'authenticate':
                raise ValueError('Use the host credential injection script; ACP login is disabled.')
            if params.get('cwd') is not None:
                params['cwd'] = self.container_path(params['cwd'])
            if params.get('additionalDirectories') is not None:
                if not isinstance(params['additionalDirectories'], list):
                    raise ValueError('Additional directories must be an array.')
                params['additionalDirectories'] = [
                    self.container_path(p) for p in params['additionalDirectories']]
            if method == 'session/prompt':
                params = self.resource_paths(params)
        except ValueError as exc:
            if 'id' in message:
                send(self.client, error(message, -32602, str(exc)))
            return
        if 'id' in message:
            self.pending[message['id']] = method
        send(self.agent, {**message, 'params': params})

    def from_agent(self, message):
        method = message.get('method')
        if method is not None:
            if method == 'session/request_permission' and 'id' in message:
                self.permissions.add(message['id'])
            elif method != 'session/update' or 'id' in message:
                if 'id' in message:
                    send(self.agent, error(message, -32601,
                         'This bridge does not expose host tools or client extensions.'))
                return
        else:
            method = self.pending.pop(message.get('id'), None)
            if method is None:
                return
            if method == 'initialize' and isinstance(message.get('result'), dict):
                # Credentials already live in the proxy. Never offer commands
                # from the container as host-side login/installation helpers.
                message['result']['authMethods'] = []
        send(self.client, self.host_paths(without_meta(message)))


def relay(command, root):
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    selector = selectors.DefaultSelector()
    bridge = Bridge(root, sys.stdout.buffer, process.stdin)
    buffers = {'client': bytearray(), 'agent': bytearray()}
    deadline = None
    try:
        selector.register(sys.stdin.buffer, selectors.EVENT_READ, 'client')
        selector.register(process.stdout, selectors.EVENT_READ, 'agent')
        while True:
            for key, _ in selector.select(timeout=0.2):
                source = key.data
                data = os.read(key.fd, 65536)
                if not data:
                    if buffers[source].strip():
                        raise ValueError('Incomplete ACP frame at EOF.')
                    if source == 'agent':
                        return process.wait(timeout=5)
                    selector.unregister(key.fileobj)
                    process.stdin.close()
                    # Give the adapter time to exit on EOF; don't leave a
                    # hanging host bridge if it stops consuming the stream.
                    deadline = time.monotonic() + 5
                    continue
                buffer = buffers[source]
                buffer.extend(data)
                while b'\n' in buffer:
                    line, _, rest = buffer.partition(b'\n')
                    buffer[:] = rest
                    if len(line) > MAX_FRAME:
                        raise ValueError('ACP frame exceeds 32 MiB.')
                    if not line.strip():
                        continue
                    message = json.loads(line, parse_constant=reject_json_constant)
                    validate_message(message)
                    if source == 'client':
                        bridge.from_client(message)
                    else:
                        bridge.from_agent(message)
                if len(buffer) > MAX_FRAME:
                    raise ValueError('ACP frame exceeds 32 MiB.')
            if deadline is not None and time.monotonic() >= deadline:
                raise ValueError('ACP adapter did not exit after stdin closed.')
    finally:
        selector.close()
        process.stdin.close()
        if process.poll() is None:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        process.stdout.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace-folder', type=Path, default=ROOT,
                        help='host workspace (default: the checkout containing this script)')
    args = parser.parse_args()
    root = args.workspace_folder.resolve()
    git_agent = importlib.import_module('git-agent')
    container = git_agent.running_container(root)
    # The running container's mounts, not the TOML edited for its next creation.
    if git_agent.check_container(container, root) not in ('shared', 'unsafe_shared'):
        raise ValueError('ACP needs git.isolation = "shared" or "unsafe_shared"; set it and acp.enabled, then run '
                         'devcontainer up --remove-existing-container.')
    return relay(['docker', 'exec', '-i', '--user', 'node', '--workdir', '/workspace',
                  '--env', 'CLAUDE_CODE_EXECUTABLE=/usr/local/bin/claude',
                  container, '/usr/local/bin/claude-agent-acp'], root)


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    signal.signal(signal.SIGHUP, lambda *_: sys.exit(129))
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except (ValueError, OSError, KeyError, IndexError, subprocess.SubprocessError) as exc:
        sys.exit(f'error: {exc}')
