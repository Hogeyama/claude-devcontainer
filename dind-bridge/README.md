`dind-bridge` is maintained in this repository and requires Node.js 22 or
newer. Edit it directly here. Run `src/main.mjs`; it needs no build step.

The sources come from `contrib/dind-bridge` and `src/docker/embed/dind-bridge-*.mjs`
of nix-agent-sandbox, trimmed to how this repository uses them:
`--api` is always given, so the relays serve Docker on loopback TCP only, and
`--docker-host` is always a Unix socket.

The bridge is not a Docker authorization boundary. It gives access to the
whole isolated DinD API and relays published TCP ports. The agent firewall
rejects direct DinD ports 2375/2376; DinD exposes only its UNIX socket.
The socket volume is mounted read-only in agent, which does **not** make
Docker API operations read-only.
