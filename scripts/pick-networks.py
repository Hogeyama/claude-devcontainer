#!/usr/bin/env python3
"""Choose this workspace's two internal /24s and write them for compose.

Docker cannot pick them: the agent's firewall and proxy settings name the proxy
and DinD by fixed addresses in them. So the first free pair in 10.231.x (outside
Docker's default pools) is chosen once, kept while it stays free, and written
to .devcontainer/.generated/networks.env, which compose reads below
settings.env. A value set in subnets of claude.toml is used instead.

Usage: pick-networks.py PROXY_NET DIND_NET, each the set value or empty; run
from the workspace root (initializeCommand).
"""
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys

OUT = Path('.devcontainer/.generated/networks.env')
NAMES = ('CLAUDE_DEVCONTAINER_PROXY_NET', 'CLAUDE_DEVCONTAINER_DIND_NET')
CANDIDATES = [f'10.231.{third}' for third in range(70, 256)]


def docker(*arguments):
    return subprocess.run(['docker', *arguments], stdout=subprocess.PIPE, text=True, check=True).stdout


def subnet(net):
    return ipaddress.ip_network(net + '.0/24')


def used_subnets(root):
    """The IPv4 subnets of Docker's networks, except this workspace's own."""
    projects = set(docker('ps', '-a', '--filter', f'label=devcontainer.local_folder={root}',
                          '--format', '{{.Label "com.docker.compose.project"}}').split())
    networks = docker('network', 'ls', '-q').split()
    used = []
    if networks:
        for network in json.loads(docker('network', 'inspect', *networks)):
            if (network.get('Labels') or {}).get('com.docker.compose.project') in projects:
                continue
            for config in (network.get('IPAM') or {}).get('Config') or []:
                address = ipaddress.ip_network(config.get('Subnet', '::/128'), strict=False)
                if address.version == 4:
                    used.append(address)
    return used


def previous():
    """The pair written last time."""
    try:
        lines = OUT.read_text().splitlines()
    except FileNotFoundError:
        return {}
    return dict(line.split('=', 1) for line in lines if '=' in line and not line.startswith('#'))


def choose(set_values, kept, used):
    """{name: net} for the names without a set value."""
    used = list(used) + [subnet(net) for net in set_values.values() if net]
    chosen = {}
    for name in NAMES:
        if set_values.get(name):
            continue
        net = kept.get(name)
        if net not in CANDIDATES or any(subnet(net).overlaps(other) for other in used):
            net = next((candidate for candidate in CANDIDATES
                        if not any(subnet(candidate).overlaps(other) for other in used)), None)
            if net is None:
                raise SystemExit(f'error: no free 10.231.x.0/24 for {name}; set subnets in .devcontainer/claude.local.toml.')
        used.append(subnet(net))
        chosen[name] = net
    return chosen


def main():
    set_values = dict(zip(NAMES, sys.argv[1:3]))
    try:
        # Nothing to choose when both are set.
        used = [] if all(set_values.get(name) for name in NAMES) else used_subnets(os.getcwd())
    except (OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(f'error: cannot list Docker networks to choose subnets: {error}')
    chosen = choose(set_values, previous(), used)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    (OUT.parent / '.gitignore').write_text('*\n')
    text = ('# Written by initializeCommand (scripts/pick-networks.py). subnets in\n'
            '# claude.toml, through settings.env, replaces these.\n'
            + ''.join(f'{name}={net}\n' for name, net in chosen.items()))
    temporary = OUT.with_name(OUT.name + '.tmp')
    temporary.write_text(text)
    temporary.replace(OUT)


if __name__ == '__main__':
    main()
