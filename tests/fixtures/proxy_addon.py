"""Production policy, with accepted upstream connections sent to the local fixture server."""
import os
import sys
sys.path.insert(0, '/opt/proxy')
from addon import PolicyAddon


class FixturePolicy(PolicyAddon):
    def server_connect(self, data):
        super().server_connect(data)
        if data.server.error is None:
            hostname = data.server.address[0]
            data.server.sni = hostname
            data.server.address = (os.environ.get('GIT_FIXTURE_HOST', '127.0.0.1') if hostname == 'github.com' else '127.0.0.1', 9443)


addons = [FixturePolicy()]
