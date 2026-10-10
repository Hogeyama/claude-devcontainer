#!/bin/sh
# As root: trust the proxy CA and allow egress only to the proxy and DinD, then
# re-run this script as node without capabilities.
set -eu
if [ "${1:-}" != --as-node ]; then
    cp /public-ca/mitmproxy-ca-cert.pem /usr/local/share/ca-certificates/managed-proxy.crt
    update-ca-certificates >/dev/null
    /usr/local/bin/init-firewall.sh
    exec setpriv --reuid=node --regid=node --init-groups \
        --bounding-set=-all --inh-caps=-all --ambient-caps=-all --no-new-privs \
        "$0" --as-node "$@"
fi
shift
readlink /proc/self/ns/net > /run/dind-bridge/base-netns
rm -f /run/dind-bridge/bridge.sock
/usr/local/bin/dind-bridge serve \
    --socket /run/dind-bridge/bridge.sock \
    --docker-host unix:///var/run/dind/docker.sock \
    --publish-host "$CLAUDE_DEVCONTAINER_DIND_ADDRESS" --publish-ip 0.0.0.0 \
    --api tcp://127.0.0.1:2375 \
    </dev/null >>/run/dind-bridge/serve.log 2>&1 &
exec "$@"
