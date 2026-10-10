# Dev Container A1b + DinD Implementation Plan

> **For agentic workers:** Use subagent-driven-development to implement and review each task. The user explicitly waived human review of this plan; execute continuously.

**Goal:** Claude Code and human-triggered builds use a mandatory credential-injecting proxy, while dind-bridge provides offline Docker inside Bash sandbox namespaces.

**Architecture:** Three Compose services: agent, proxy, rootless dind. Only proxy has external networking. Fixed Python rules constrain GitHub authentication and Anthropic Messages features; no GraphQL parsing or policy config engine.

**Tech Stack:** Docker Compose, Node.js 22, locally maintained dind-bridge, Python 3.12 / mitmproxy 11, unittest, shell.

## Global Constraints

- Read `docs/superpowers/specs/2026-10-10-devcontainer-a1b-dind-design.md`, including the VS Code and Anthropic sections approved in conversation.
- No P1 guarantee, no GraphQL parser, no generic policy config, no broker. All network rules belong in `proxy/addon.py`.
- GitHub's injected fine-grained PAT selects only approved private repositories. GitHub enforces repository authorization; do not pretend a mock verifies GitHub's own behavior.
- Secrets live outside the workspace and are mounted only into proxy. Agent receives dummy credentials and the public CA certificate only.
- No host Docker socket or host home mounts. No direct egress from agent or dind. No network namespace sharing between agent and dind.
- Human-triggered builds in the container are subject to the same proxy restrictions as Claude. Host execution is outside this boundary.
- DinD images are preloaded. No runtime registry access or online build dependencies in the initial version.
- Maintain dind-bridge in this repository without upstream version tracking or a separate license notice, as authorized by its owner. Verify its local checksum.
- Use Python stdlib unittest and real mitmproxy flow objects for unit tests; real services only in integration tests. Docker resources have random test project names and are removed in finally/traps. Never remove existing user resources.
- Relevant skills: security-constraints (secret/control-socket boundary), test-driven-development, git-commit, verification-before-completion. This is a new Python/shell project; the existing Deno/Bun-specific test command conventions do not apply.
- Run focused tests during iteration and the applicable complete suite before commit. Each implementation task is followed by an independent code review.

## File map and interfaces

| Area | Files | Interface |
| --- | --- | --- |
| Proxy | `proxy/addon.py`, `proxy/addon_test.py`, `proxy/requirements.txt`, `proxy/Dockerfile`, `proxy/entrypoint.py` | HTTP regular proxy `:8080`, private tmpfs credential snapshot, public certificate volume |
| Container | `.devcontainer/devcontainer.json`, `.devcontainer/compose.yaml`, `.devcontainer/Dockerfile`, `.devcontainer/entrypoint.sh`, `.devcontainer/init-firewall.sh`, `.devcontainer/managed-settings.json` | agent UID1000, workspace `/workspace`, Docker `tcp://127.0.0.1:2375` |
| Bridge | `dind-bridge/dind-bridge`, `dind-bridge/dind-bridge.sha256`, `dind-bridge/README.md`, `scripts/prepare.sh` | local executable, env-file, serve |
| Verification | `tests/proxy_integration_test.py`, `tests/devcontainer_integration_test.py`, `tests/fixtures/`, `README.md` | explicit Docker test command; no live account required |

Dev Containers builds/starts without credentials. `scripts/inject-credentials.py` accepts host `GITHUB_TOKEN` and exactly one of `CLAUDE_CODE_OAUTH_TOKEN`/`ANTHROPIC_API_KEY`, validates the matching agent/proxy by Docker labels and workspace mount, injects JSON over Docker exec stdin, then exits; work uses ordinary `devcontainer exec` or VS Code terminals. Proxy credentials live only at `/run/proxy-credentials/credentials.json` on private tmpfs (UID1000,0400). Auth mode is selected by host injection, not by a request. Subprocess environments are sanitized; the agent uses dummy credentials independently of proxy auth mode. Public CA: `/public-ca/mitmproxy-ca-cert.pem`. Private CA: `/home/mitmproxy/.mitmproxy`. Proxy policy failure returns a fixed 403 response, except header-denied requests declaring a body are disconnected immediately to avoid buffering. Diagnostics never include input values.

## Task 1: Mandatory proxy and fixed request policy

**Files:** Create the `proxy/` files listed above and minimal `.gitignore` / test instructions as needed.

**Consumes:** A bounded stdin snapshot with credentials and auth mode, injected after startup. **Produces:** Deployable proxy image plus runnable unit tests. The addon class is `PolicyAddon`, with mitmproxy hooks; module `addons` registers its instance. Constructor may accept test credentials/resolver without introducing runtime policy configuration.

- [ ] Write failing behavioral tests with real `mitmproxy.http.Request` / test flow objects. Include this basic contract:

```python
flow.request = http.Request.make(
    "POST", "https://api.github.com/graphql", b'{"query":"query { viewer { login } }"}',
    {"Authorization": "Bearer attacker", "Cookie": "user_session=attacker"},
)
addon.request(flow)
assert flow.response is None
assert flow.request.headers["Authorization"] == "Bearer managed-test-pat"
assert "Cookie" not in flow.request.headers
```

- [ ] Cover Git smart HTTP Basic auth; decoded credential query parameters; duplicate headers; URL userinfo; authority mismatch; invalid paths; plaintext, unknown host/port, WebSocket, CONNECT passthrough. GraphQL body must be forwarded without parsing, including syntactically invalid GraphQL (upstream owns syntax validation).
- [ ] Cover normal Claude Messages (text, system, local tool definitions, tool use/results, inline base64 images, thinking) and token count. Reject MCP connector, all server-executed tools, external URL image/document sources, unknown behavioral fields/types, duplicate JSON keys, malformed/compressed/oversized bodies. Bound JSON recursion and body size. Do not recursively treat arbitrary user text or JSON schema properties as API control fields.
- [ ] Implement a closed supported Anthropic API subset. Keep accepted request-level keys, content blocks and tool keys explicit. Treat tool-use input and tool-result text as data; nested tool-result content blocks still need source/type checks. Whitelist necessary beta headers, reject unknown ones. No arbitrary `x-*` auth headers/cookies survive injection.
- [ ] Use regular proxy, `connection_strategy=lazy`, `upstream_cert=false`, `rawtcp=false`, `http2=false`, no flow dumps. Refuse requests before upstream connects. Preserve SSE response streaming while buffering requests for inspection. Denied requests must not cause name resolution.
- [ ] Validate CONNECT authority and decrypted Host/authority consistently, enforce HTTPS443. Keep TLS certificate verification enabled. In `server_connect`, resolve permitted names, reject all non-public answers, pin checked address while retaining hostname SNI, and restore address identity for connection reuse as upstream does.
- [ ] Fail closed on credential-loading errors at process startup; fail closed on policy exceptions per request. Fixed reason labels only in logs. `entrypoint.py` creates CA, atomically exports only public PEM, then runs mitmdump with required options.
- [ ] Run `python -m unittest discover -s proxy -p '*_test.py' -v` in the pinned mitmproxy environment. Record RED/GREEN evidence, self-review, commit.

## Task 2: Dev Container and offline DinD wiring

**Files:** Create `.devcontainer/`, `dind-bridge/`, `scripts/prepare.sh`, `.dockerignore`, `.gitignore` entries.

**Consumes:** Task 1 proxy image and secret/CA interface. **Produces:** Dev Containers application runnable without authentication; local bridge executable.

- [ ] Add runnable configuration checks for no host socket, no secret mounts into agent/dind, no dind external network, no runtime bind mount of addon, and fixed-address/firewall consistency. Exercise entrypoint failure when CA/bridge preparation fails; avoid tests that merely duplicate every YAML line.
- [ ] Keep the bridge executable and checksum in this repository. Maintain changes locally; no sibling repository is required. `prepare.sh` verifies checksum and validates prerequisites without printing secret values or writing secrets into the repo.
- [ ] Compose has two internal networks and one external-capable network. Prefer Docker bridge `com.docker.network.bridge.gateway_mode_ipv4=isolated` for internal networks so the host bridge has no address; verify supported Docker version. Disable IPv6. Fixed agent/proxy addresses on `10.231.70.0/24`, agent/dind on `10.231.71.0/24`; no host published ports. Proxy `.10`, agent `.20`, dind `.10` on its separate network. Disable proxy IP forwarding.
- [ ] agent startup firewall defaults DROP, blocks embedded DNS before loopback accept, permits proxy `10.231.70.10:8080` and dind `10.231.71.10` TCP except2375/2376, and established replies. IPv6 blocked. Startup is root solely for CA/firewall initialization, then drop to UID1000 and remove effective/permitted/inheritable/ambient capabilities; no sudo. `remoteUser` is node.
- [ ] Agent image installs Node22, Claude CLI pinned to tested version, Docker CLI, gh, git, curl, ripgrep, Python3, bubblewrap, socat, iptables, util-linux, and Java tooling for the build-trigger regression. Export public CA to system trust and Node/Git/curl. Proxy variables point to fixed IP. `NO_PROXY` only loopback and dind; firewall still enforces destination restrictions.
- [ ] Start bridge as node in entrypoint: record base netns; serve socket `/run/dind-bridge/bridge.sock`, backend `/var/run/dind/docker.sock`, publish host fixed dind address, publish IP0.0.0.0, API loopback2375. Generate env-file during image build with final node/script paths. Managed Claude settings enable sandbox, failIfUnavailable, disallow unsandboxed commands, allowAllUnixSockets, and the required weaker nested sandbox setting. Mount no host credential or agent sockets.
- [ ] dind uses rootless image pinned to a verified digest, privileged outer container, UID1000, explicit command `dockerd --host=unix:///run/user/1000/docker.sock`. Socket is tmpfs UID1000 mode0700 and read-only in agent, data volume private to dind. Do not expose dockerd TCP or attach proxy credentials/network.
- [ ] Run `docker compose -f .devcontainer/compose.yaml config --quiet` with authentication variables absent; build images and check startup as nonroot. Record versions/digests, focused tests, commit.

## Task 3: End-to-end verification and operating instructions

**Files:** `tests/proxy_integration_test.py`, `tests/devcontainer_integration_test.py`, `tests/fixtures/`, `README.md`, fixes in earlier task files if tests expose failures.

**Consumes:** Both production images/Compose. **Produces:** Reproducible local verification and accurate usage instructions.

- [ ] Use unique Docker project/resources and dummy credentials for tests. Add cleanup in finally/traps. Missing Docker is reported explicitly, not counted as a passing security check.
- [ ] Real mitmproxy plus mock HTTPS upstream observes normalized injected credentials and receives no denied request. Test attacker Bearer/Basic/cookies, decoded auth parameters, duplicate auth headers, mismatched authorities, bad TLS certificates, raw TCP/SOCKS/HTTP paths, repeated requests on reused connections, chunked/oversized Messages, rejected server tools and URL sources. Test-only hostname/IP mapping must not introduce a production bypass switch.
- [ ] Start the topology using actual `devcontainer up` without authentication variables; inject synthetic values through the host injection command and run actual `devcontainer exec` in OAuth and API-key modes. From agent outside Bash sandbox, verify allowed proxy request, blocked MCP payload, blocked external IP/DNS/IPv6/host and proxy-stop behavior. Run a tiny Gradle/Java build fixture that attempts the MCP request as a build hook, and assert proxy rejection without contacting Anthropic.
- [ ] Load a local test image into rootless DinD; use Docker API to run a published TCP service. Check Docker ping and published service both directly and in an `unshare` network namespace using bridge snippet. Restart agent and repeat. Test DinD bridge and host network containers cannot reach external IPs/DNS/host services.
- [ ] Exercise actual Claude Code Bash sandbox with dummy credentials and a controlled API fixture if feasible; never consume existing host credentials silently. If actual CLI/Testcontainers cannot run, report the exact limitation separately from namespace surrogate tests.
- [ ] README documents host requirements, credential-free Dev Containers startup, environment input and one-time host credential injection followed by normal CLI/editor work, first Claude task and stopping the selected Compose project. Linked usage/policy/troubleshooting/testing docs cover renewal, image preload, offline builds, Testcontainers/Ryuk, CA, review/rebuild of rules, editor conditions, host boundaries and verification.
- [ ] README states observed results and remaining live GitHub/Claude limitations. Actual GitHub write tests require dedicated allowed/denied repos and user-supplied restricted PAT; don't create issues or mutate user repos implicitly. No unqualified A1b ◎ claim without its operational preconditions and evidence.
- [ ] Run unit + integration suites once after final changes, `git diff --check`, and independent whole-change review. Fix correctness findings with targeted tests and separate commits. Commit final docs and retain evidence in the progress ledger.

## Acceptance

The result must be runnable, fail closed if proxy is unavailable, prevent attacker-selected authentication from reaching the upstream, reject Messages features that cause third-party requests, and expose offline Docker API/published ports through the local bridge. Live-service assumptions and any checks not performed remain explicit; never weaken policy to make a compatibility test pass.
