"""Production policy, with accepted upstream connections sent to the local fixture server."""
import sys
sys.path.insert(0, '/opt/proxy')
from addon import PolicyAddon


class FixturePolicy(PolicyAddon):
    def server_connect(self, data):
        super().server_connect(data)
        if data.server.error is None:
            data.server.sni = data.server.address[0]
            data.server.address = ('127.0.0.1', 9443)


addons = [FixturePolicy()]
