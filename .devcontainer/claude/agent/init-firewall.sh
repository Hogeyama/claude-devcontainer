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
