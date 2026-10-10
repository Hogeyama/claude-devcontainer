# Runtime authentication and Dev Containers lifecycle

> Use subagent-driven-development. The user requested these review changes and approved one-time proxy-only injection followed by normal CLI/editor work; plan human review remains waived.

**Goal:** Start/build through Dev Containers without credentials, then inject host environment credentials once into proxy before normal CLI/editor work. Never persist tokens to host files or container image layers.

**Architecture:** The proxy starts with its CA and policy but denies requests until credentials exist. A fixed-path JSON snapshot on a proxy-only tmpfs holds the GitHub token, Anthropic token and auth mode. A host command passes a bounded JSON message through Docker exec stdin to an injector in proxy, then exits. Ordinary Dev Containers CLI commands and VS Code terminals use the selected proxy credentials without an auth-mode environment variable. The agent continues using dummy credentials.

**Tech stack:** Existing Python, mitmproxy, Docker Compose and Dev Containers CLI.

## Constraints and decisions

- User review comments 35/37/39 require claude setup-token examples, environment inputs, no persistent secret files, and Dev Containers managing build/up. Comment33 asks to clarify that only duplicate instances of this fixed-subnet topology conflict, not unrelated Compose projects.
- The user explicitly chose proxy-only injection. A root-owned0400 file in agent was suggested as an alternative; keep the file only in proxy's private tmpfs, owned by its runtime UID with0400 permissions. Agent and DinD have no mount or control API access to it.
- Keep all outbound policy, GitHub authorization assumptions, no-GraphQL-parser decision, CA separation, firewall and offline DinD behavior.
- No credentials in Docker creation config, command arguments, logs, workspace, or host secret files. Host accepts GITHUB_TOKEN and exactly one of CLAUDE_CODE_OAUTH_TOKEN/ANTHROPIC_API_KEY. Credentials pass to proxy injector via stdin; subprocesses otherwise receive a sanitized environment.
- Credential installation is atomic and bounded; malformed/missing credentials fail closed. No stale fallback after snapshot deletion or proxy restart. Re-injection updates subsequent requests without rebuilding/restarting. One proxy credential set per development environment; no multi-user broker or per-agent credential delegation.
- Proxy tmpfs survives only the running container. Its credentials remain usable until replaced or the proxy stops. This supports work from editor terminals after injection.
- User requested UID/subnet customization later; do not implement it now. No real credentials/live-account actions in tests.
- Read existing design, security-constraints, test-policy, git-commit and verification-before-completion. Actual user changes override older file-secret and Compose-up instructions in the existing spec/plan.

## Task1: Credential-free proxy startup and runtime credential snapshot

Files: proxy/addon.py,entrypoint.py,Dockerfile,.dockerignore, new proxy/credentials.py; proxy unit tests; tests/proxy_integration_test.py and fixtures; proxy tmpfs mount in .devcontainer/compose.yaml.

Interface: /run/proxy-credentials/credentials.json; JSON exact keys github_token,anthropic_token,auth_mode (api-key/oauth). python /opt/proxy/credentials.py inject reads JSON from stdin, validates and atomically installs0400 snapshot without echoing inputs. Fixed production path, helper functions may accept test paths. Mount tmpfs at directory, uid/gid1000 mode0700, no agent/dind sharing.

- [ ] Write RED behavioral tests for startup without credentials, denial without upstream/DNS, injection enabling correct auth, invalid input preserving an existing valid snapshot atomically, replacement on reused connection, deletion/restart denying again, malformed on-disk data denying without echo.
- [ ] Implement independent snapshot helper and dynamic per-request reads. Remove startup requirement for tokens; keep CA startup fail-closed.
- [ ] Adapt real TLS fixture to use new injection path, preserving existing policy tests and no production bypass.
- [ ] Build proxy, run units and real-wire integration, inspect tmpfs/config/log exposure with synthetic markers. Commit and independent review before Task2.

## Task2: Host credential injection, Dev Containers lifecycle, docs and end-to-end verification

Files: scripts/inject-credentials.py,prepare.sh; .devcontainer/compose.yaml/devcontainer.json/claude.sh as needed; tests for injection/config/startup/whole topology; README,docs/usage.md,testing.md,network-policy.md,troubleshooting.md and existing design/plan corrections.

- [ ] Remove Compose secrets/token interpolation and all credential checks from prepare.sh. Devcontainer up/build must work with all authentication variables absent.
- [ ] Host injection command discovers exactly the running agent for this workspace and matching proxy by trusted Docker labels, rejecting absent/ambiguous/mismatched targets. It must not run up/build itself or disclose token values on errors.
- [ ] Injection command validates environment inputs, waits existing readiness, sends bounded JSON via Docker exec stdin, strips real auth from Docker's environment, then exits. Unexpected arguments fail; no command execution or compatibility wrapper remains.
- [ ] Test credential-free startup, injection order/target, error handling and env/log secrecy. After one injection, actual Dev Containers CLI commands use either proxy auth mode without an auth-mode remote environment. VS Code GUI and native Claude-to-proxy auth-mode coupling remain unverified.
- [ ] Adapt existing runtime tests; preserve actual Claude Bash+DinD, TLS policy, Gradle rejection and cleanup recovery coverage. No real API/token generation.
- [ ] README follows devcontainer up then host setup-token/environment preparation then one injection command followed by ordinary CLI or VS Code work. Setup-token prints a token for the user to export; never capture entire interactive output as if only token. API-key remains optional. Explain duplicate-topology subnet conflict in plain language. Keep technical verification in docs/testing.md.
- [ ] Update comments33/35/37/39 in one scoped commit each where practical (37/39 implementation depends on shared auth interface). Complete focused then full affected tests, independent review, push local PR and reply with exact change links. User resolves review threads.
