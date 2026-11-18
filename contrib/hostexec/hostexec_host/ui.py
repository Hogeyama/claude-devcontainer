"""The approval inbox, for a browser or `hostexec review` on the host.

It listens on loopback, which any local process or web page can reach, so
being reachable grants nothing. Authority is the UI token: every API call
must carry it in an Authorization header and come from the exact Origin,
which a cross-site page can do neither of. An exact Host defeats DNS
rebinding. The API lists what is waiting and decides one request by its id,
nothing else.

Adapted from strait's `review web` (nix-agent-sandbox/contrib/strait, MIT).
"""
import json
import time

from .api import Handler, same_secret
from .run import INHERITED_ENV

MAX_BODY = 4096
HEADERS = (
    ('Cache-Control', 'no-store'),
    ('Content-Security-Policy',
     "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
     "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
    ('Cross-Origin-Resource-Policy', 'same-origin'),
    ('Cross-Origin-Opener-Policy', 'same-origin'),
    ('Referrer-Policy', 'no-referrer'),
    ('X-Content-Type-Options', 'nosniff'),
    ('X-Frame-Options', 'DENY'),
)


def ui_handler(authority, token, approvals, assets):
    """The request handler for the inbox. `authority` gives `127.0.0.1:PORT`,
    as the browser sends it in Host; `assets` maps each page's path to its
    (text, content type)."""

    class UiHandler(Handler):
        def json(self, status, value):
            self.reply(status, json.dumps(value), 'application/json', HEADERS)

        def do_any(self):
            host = authority()
            path, query, _ = self.path.partition('?')
            if self.headers.get('Host') != host or query:
                return self.json(403, {'error': 'forbidden'})
            if self.command == 'GET':
                if path not in assets:
                    return self.json(404, {'error': 'not found'})
                text, content_type = assets[path]
                return self.reply(200, text, content_type, HEADERS)
            # Every API call, listing included, is a POST so that the browser
            # sends Origin. Authorization is not a CORS-safelisted header, and
            # no CORS headers are ever sent, so no other origin can make the call.
            site = self.headers.get('Sec-Fetch-Site')
            if self.command != 'POST' or self.headers.get('Origin') != f'http://{host}' \
                    or not same_secret(self.headers.get('Authorization'), f'Bearer {token}') \
                    or site not in (None, 'same-origin'):
                return self.json(403, {'error': 'forbidden'})
            raw = self.body(MAX_BODY)
            try:
                body = None if raw is None else json.loads(raw or b'{}')
            except ValueError:
                body = None
            if not isinstance(body, dict):
                return self.json(400, {'error': 'bad request'})
            if path == '/api/pending':
                pending = [{'id': h.id, 'argv': h.exec.argv, 'cwd': h.exec.cwd,
                            'requestedCwd': h.exec.requested_cwd, 'env': h.exec.env,
                            'since': h.since, 'expiresAt': h.expires_at}
                           for h in approvals.list()]
                return self.json(200, {'pending': pending, 'inheritedEnv': list(INHERITED_ENV),
                                       'now': int(time.time() * 1000)})
            if path == '/api/decision':
                id, approve = body.get('id'), body.get('approve')
                if not isinstance(id, str) or not isinstance(approve, bool):
                    return self.json(400, {'error': 'bad request'})
                if approvals.decide(id, approve):
                    return self.json(200, {'ok': True})
                return self.json(404, {'error': 'no such request: decided, expired, or withdrawn'})
            return self.json(404, {'error': 'not found'})

    return UiHandler
