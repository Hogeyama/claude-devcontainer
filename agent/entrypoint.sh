#!/bin/sh
# As root: trust the proxy CA and allow egress only to the proxy and DinD, then
# re-run this script as node without capabilities.
set -eu
if [ "${1:-}" != --as-node ]; then
    cp /public-ca/mitmproxy-ca-cert.pem /usr/local/share/ca-certificates/managed-proxy.crt
    update-ca-certificates >/dev/null
    /usr/local/bin/init-firewall.sh
    # Managed hooks, so that the agent cannot turn the masking off.
    sumi=/etc/claude-code/managed-settings.d/90-sumi.json
    if [ "${CLAUDE_DEVCONTAINER_SUMI:-0}" = 1 ]; then
        install -m 644 /usr/local/share/claude-devcontainer/sumi-settings.json "$sumi"
    else
        rm -f "$sumi"
    fi
    exec setpriv --reuid=node --regid=node --init-groups \
        --bounding-set=-all --inh-caps=-all --ambient-caps=-all --no-new-privs \
        "$0" --as-node "$@"
fi
shift
if [ "${CLAUDE_DEVCONTAINER_DIND:-0}" != 1 ]; then
    exec "$@"
fi
/usr/local/bin/dind-bridge \
    --docker-host unix:///var/run/dind/docker.sock \
    --publish-host "$CLAUDE_DEVCONTAINER_DIND_ADDRESS" --publish-ip 0.0.0.0 \
    --api tcp://127.0.0.1:2375 \
    </dev/null >>/run/dind-bridge/serve.log 2>&1 &
exec "$@"
