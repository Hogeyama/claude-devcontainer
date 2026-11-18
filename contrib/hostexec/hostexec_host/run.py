"""Running an approved command on the host, streaming its output.

Process-group handling adapted from strait (nix-agent-sandbox/contrib/strait, MIT).
"""
import atexit
import os
import signal
import subprocess
import threading
import time

# Taken from the server's own environment, so that `nix` or `docker` resolve
# as in the host shell. Nothing else is inherited.
INHERITED_ENV = ('PATH', 'HOME')

# How long a stopped command's processes get after SIGTERM before SIGKILL.
KILL_GRACE_SECONDS = 5

MASK = b'[masked by hostexec]'


def exec_env(request, host):
    env = {name: host[name] for name in INHERITED_ENV if name in host}
    env.update(request.env)
    return env


class Masker:
    """Replaces secrets in a byte stream that arrives in chunks: it holds back
    enough of each chunk's tail that a secret split across chunks is still
    found."""

    def __init__(self, secrets):
        self._secrets = [s.encode() for s in secrets if len(s) >= 8]
        self._keep = max((len(s) - 1 for s in self._secrets), default=0)
        self._pending = b''

    def push(self, chunk):
        if not self._secrets:
            return chunk
        data = self._pending + chunk
        for secret in self._secrets:
            data = data.replace(secret, MASK)
        # Only a tail that could be the start of a secret is held back, so
        # output that ends a line or a word passes at once.
        cut = len(data) - self._partial(data)
        self._pending = data[cut:]
        return data[:cut]

    def _partial(self, data):
        """The length of the longest tail of `data` that starts some secret."""
        for k in range(min(self._keep, len(data)), 0, -1):
            tail = data[-k:]
            if any(s[:k] == tail for s in self._secrets):
                return k
        return 0

    def end(self):
        rest, self._pending = self._pending, b''
        return rest


# Groups of runs not yet over, by process group ID.
_owned = set()
_owned_lock = threading.Lock()


@atexit.register
def kill_all():
    """SIGKILL every group still running, as the server exits."""
    with _owned_lock:
        groups = list(_owned)
    for group in groups:
        try:
            os.killpg(group, signal.SIGKILL)
        except OSError:
            pass


def _stop_group(group, grace):
    """SIGTERM the group, then SIGKILL it if still there after `grace`."""
    def send(sig):
        try:
            os.killpg(group, sig)
            return True
        except OSError:
            return False
    if not send(signal.SIGTERM):
        return
    deadline = time.monotonic() + grace
    while True:
        time.sleep(0.05)
        there = send(0)
        if there and time.monotonic() < deadline:
            continue
        # A group seen gone is not signalled again: its ID may be reused.
        if there:
            send(signal.SIGKILL)
        return


class Run:
    """The command running with no stdin. Output goes to `on_output(stream,
    data)` as it comes, with `secrets` masked; then `on_exit(exit_code,
    signal_name)`, once. `stop()` stops it, and the process group it runs in."""

    def __init__(self, request, secrets, on_output, on_exit, grace=KILL_GRACE_SECONDS):
        self._on_output = on_output
        self._on_exit = on_exit
        self._grace = grace
        self._lock = threading.Lock()
        self._done = False
        self._stopping = None
        self._maskers = {'stdout': Masker(secrets), 'stderr': Masker(secrets)}
        try:
            # In a session, so a process group, of its own, so that stopping
            # it reaches what it started too. A child that makes its own
            # group (setsid) still escapes.
            self._process = subprocess.Popen(
                request.argv, cwd=request.cwd, env=exec_env(request, os.environ),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=True)
        except (OSError, ValueError) as e:
            self._process = None
            self._done = True
            on_output('stderr', f'hostexec: {e}\n'.encode())
            on_exit(127, None)
            return
        with _owned_lock:
            _owned.add(self._process.pid)
        threading.Thread(target=self._wait, daemon=True).start()

    def _read(self, name, pipe):
        masker = self._maskers[name]
        while chunk := os.read(pipe.fileno(), 65536):
            self._emit(name, masker.push(chunk))
        pipe.close()

    def _emit(self, name, data):
        if data:
            self._on_output(name, data)

    def _wait(self):
        readers = [threading.Thread(target=self._read, args=(name, getattr(self._process, name)),
                                    daemon=True)
                   for name in ('stdout', 'stderr')]
        for r in readers:
            r.start()
        # Over when the command has exited and its output is all read, as a
        # child it left behind may still be writing. The command is left
        # unreaped meanwhile, so its group's ID stays ours to signal.
        os.waitid(os.P_PID, self._process.pid, os.WEXITED | os.WNOWAIT)
        for r in readers:
            r.join()
        # Done before the reaping: until then the group's ID is still ours to
        # signal, and after it a later stop() must not signal it.
        with self._lock:
            self._done = True
            stopping = self._stopping
        code = self._process.wait()
        if stopping is not None:
            stopping.join()
        with _owned_lock:
            _owned.discard(self._process.pid)
        for name, masker in self._maskers.items():
            self._emit(name, masker.end())
        if code < 0:
            self._on_exit(None, signal.Signals(-code).name)
        else:
            self._on_exit(code, None)

    def stop(self):
        with self._lock:
            if self._done or self._stopping is not None:
                return
            self._stopping = threading.Thread(
                target=_stop_group, args=(self._process.pid, self._grace), daemon=True)
            self._stopping.start()
