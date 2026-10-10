`dind-bridge` is maintained in this repository and requires Node.js 22 or
newer. Edit it directly here. Run `src/main.mjs`; it needs no build step.

It serves the DinD Docker API on the agent's `127.0.0.1:2375` and mirrors each
port a container publishes on `0.0.0.0` to the same port on the agent's
`127.0.0.1`, so that `localhost` works as it does with a local Docker. A
start or restart response is held until the container's ports are mirrored.

The sources come from `contrib/dind-bridge` and `src/docker/embed/dind-bridge-*.mjs`
of nix-agent-sandbox, trimmed to how this repository uses them: one relay in
the agent's own network namespace, with `--docker-host` always a Unix socket.

The bridge is not a Docker authorization boundary. It gives access to the
whole isolated DinD API and relays published TCP ports. The agent firewall
rejects direct DinD ports 2375/2376; DinD exposes only its UNIX socket.
The socket volume is mounted read-only in agent, which does **not** make
Docker API operations read-only.
