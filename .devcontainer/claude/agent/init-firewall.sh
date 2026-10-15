#!/bin/sh
set -eu
# Set by compose.yaml.
proxy=${CLAUDE_DEVCONTAINER_PROXY_ADDRESS:?}
dind=${CLAUDE_DEVCONTAINER_DIND_ADDRESS:?}
# IPv6 is disabled by sysctl in compose.yaml.
iptables -P INPUT DROP
iptables -P FORWARD DROP
iptables -P OUTPUT DROP
iptables -F
# Docker's embedded resolver is on loopback and forwards to the host's resolver,
# so reject DNS before accepting loopback. NAT can rewrite its port 53 first.
iptables -A OUTPUT -d 127.0.0.11 -j DROP
iptables -A OUTPUT -p udp --dport 53 -j DROP
iptables -A OUTPUT -p tcp --dport 53 -j DROP
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A INPUT -i lo -j ACCEPT
iptables -A OUTPUT -d "$proxy" -p tcp --dport 8080 -j ACCEPT
iptables -A OUTPUT -d "$dind" -p tcp -m multiport ! --dports 2375,2376 -j ACCEPT

# code-server-proxy alone may connect in, to any port: each connection goes
# to the same port of loopback, where the editor's server (18000) and the
# agent's web apps listen, on 127.0.0.1 or 0.0.0.0 alike. compose.yaml sets
# route_localnet, without which the kernel drops a packet to 127.0.0.1 that
# came from outside; INPUT's DROP still refuses every other source.
if [ "${CLAUDE_DEVCONTAINER_CODE_SERVER:-0}" = 1 ]; then
    browser=${CLAUDE_DEVCONTAINER_CODE_SERVER_PROXY_ADDRESS:?}
    iptables -t nat -A PREROUTING -s "$browser" -p tcp -j DNAT --to-destination 127.0.0.1
    iptables -A INPUT -s "$browser" -d 127.0.0.1 -p tcp -j ACCEPT
fi
