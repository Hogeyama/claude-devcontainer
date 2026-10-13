#!/usr/bin/env python3
"""Expose the running container's Claude over ACP, without host tool access.

Run this on the host. stdout is exclusively newline-delimited JSON-RPC.
The host owns this boundary: the agent cannot change it in its isolated clone.
"""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import selectors
import signal
import subprocess
import sys
import time
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[3]
MAX_FRAME = 32 * 1024 * 1024


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
        if result is not None:
            return result
        # Only client-supplied paths reach here. The host editor may use a
        # symlink alias even when Docker's label and our root are canonical.
        # Never resolve paths received from the agent on the host.
        try:
            result = mapped(str(Path(value).resolve()), self.roots)
        except (OSError, RuntimeError):
            result = None
        if result is not None:
            return result
        raise ValueError('ACP cwd must be inside this workspace.')

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
                    pass  # A resource outside this workspace is unchanged.
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
            if not isinstance(params, dict):
                raise ValueError('ACP params must be an object.')
            params = dict(params)
            # Clients often attach their host MCP config automatically,
            # including env, headers and credentials in URLs or arguments.
            # Container MCP servers must be configured inside the container.
            if 'mcpServers' in params or method in ('session/new', 'session/load', 'session/resume', 'session/fork'):
                params['mcpServers'] = []
            if method == 'initialize':
                # No fs, terminal, auth terminal, URL elicitation, or extensions.
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
        send(self.client, without_meta(message))


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
    containers = subprocess.run(
        ['docker', 'ps', '--filter', f'label=devcontainer.local_folder={root}',
         '--filter', 'label=com.docker.compose.service=agent', '--format', '{{.ID}}'],
        check=True, capture_output=True, text=True).stdout.split()
    if len(containers) != 1:
        raise ValueError('Expected exactly one running agent container for this workspace; run devcontainer up.')
    return relay(['docker', 'exec', '-i', '--user', 'node', '--workdir', '/workspace',
                  '--env', 'CLAUDE_CODE_EXECUTABLE=/usr/local/bin/claude',
                  containers[0], '/usr/local/bin/claude-agent-acp'], root)


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    signal.signal(signal.SIGHUP, lambda *_: sys.exit(129))
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        sys.exit(f'error: {exc}')
