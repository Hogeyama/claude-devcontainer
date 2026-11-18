"""Requests waiting for a person, one decision each."""
from dataclasses import dataclass
import secrets
import threading
import time

HOLD_SECONDS = 240

# What a held request comes to. GONE: the client went away first.
APPROVED, DENIED, EXPIRED, GONE = 'approved', 'denied', 'expired', 'gone'


@dataclass(frozen=True)
class Held:
    id: str
    exec: object
    # Milliseconds since the epoch, as the browser counts.
    since: int
    expires_at: int


class Approvals:
    def __init__(self, hold_seconds=HOLD_SECONDS):
        self._hold_seconds = hold_seconds
        self._lock = threading.Lock()
        self._entries = {}
        self._listeners = []

    def hold(self, exec_request, on_decision):
        """Hold a request until someone decides it or it expires; then
        `on_decision` gets the decision, once, from whichever thread made it.
        Returns the held request and a function that withdraws it (GONE)."""
        since = int(time.time() * 1000)
        held = Held(id=secrets.token_hex(16), exec=exec_request, since=since,
                    expires_at=since + int(self._hold_seconds * 1000))
        timer = threading.Timer(self._hold_seconds, lambda: self._finish(held.id, EXPIRED))
        timer.daemon = True
        with self._lock:
            self._entries[held.id] = (held, timer, on_decision)
            listeners = list(self._listeners)
        timer.start()
        for listener in listeners:
            listener(held)
        return held, lambda: self._finish(held.id, GONE)

    def _finish(self, id, decision):
        with self._lock:
            entry = self._entries.pop(id, None)
        if entry is None:
            return False
        _, timer, on_decision = entry
        timer.cancel()
        on_decision(decision)
        return True

    def list(self):
        with self._lock:
            return [held for held, _, _ in self._entries.values()]

    def decide(self, id, approve):
        """Whether a request with that id was waiting and is now decided."""
        return self._finish(id, APPROVED if approve else DENIED)

    def on_hold(self, listener):
        with self._lock:
            self._listeners.append(listener)

    def deny_all(self):
        """Deny everything still waiting, as the server stops."""
        with self._lock:
            ids = list(self._entries)
        for id in ids:
            self._finish(id, DENIED)
