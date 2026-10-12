# Local Bootstrap and Direct Git Transport

**Goal:** Start from the host's local committed HEAD and exchange work directly with the running container using ordinary Git. Startup automatically adds the host's `agent` remote. No GitHub or external repository participates.

**Architecture:** `workspace-source.py` exports a local bare snapshot of the starting commit; node imports it into the dedicated workspace volume. `git-agent.py register`, called by `initialize.sh`, adds the host remote. Git's built-in ext transport runs a fixed host command that resolves the invoking worktree, discovers its running agent, and pipes upload-pack/receive-pack through `docker exec -i --user node`. No host checkout, SSH server, exposed Git port or container access to the host Docker socket is added.

**Constraints:** Preserve host origin and unrelated agent remotes. Never share host index/config/hooks, object hardlinks or alternates. Do not copy dirty/untracked host files. Preserve completed workspace and dependency CoW state. Reject missing/ambiguous running containers. Keep the host transport independent of container IP/ID and compatible with Git worktrees. Restrict the ext permission to the host repository.

- [x] Write regression tests for unpublished/offline local startup, independent objects, excluded host settings, linked worktrees, SHA256, shallow clone and interrupted import recovery; observe failures against remote bootstrap.
- [x] Export exact HEAD into an independent bare repo with empty template, local fetch and shallow boundary support. Validate the bootstrap ref before atomically publishing seed and metadata. Keep immutable seeds for retries.
- [x] Import the local seed as node; remove external URL metadata and container-side exchange remotes. Preserve mount collision checks and completed clones.
- [x] Test remote registration with real temporary Git repos: origin preservation, unrelated agent collision, idempotence, ext escaping, actual fetch/push wire protocol through a Docker stand-in, service validation and missing/ambiguous container refusal.
- [x] Add the host transport and automatic registration. Review found shared remote configuration conflicted across linked worktrees; reproduce with a test, then resolve the invoking worktree dynamically. Verify linked-worktree and subdirectory fetches.
- [x] Replace the HTTP Git fixture with direct host-to-container fetch/push tests, including exchange with proxy stopped, automatic registration during real devcontainer up, and reconnection after recreation.
- [x] Rewrite usage, managed skill, architecture and recovery documentation for local handoff. Remove the external Git fixture and obsolete plan steps.

## Validation

- `python3 -m unittest discover -s tests -p "*_test.py"`: 123 tests, 97 passed and 26 opt-in Docker skips.
- Full real devcontainer integration: 18 passed, including actual Claude, offline npm, Testcontainers and proxy-independent direct fetch/push.
- After the worktree fix, four real-container checks for automatic registration, fetch/isolation, offline push and recreation passed again.
- `git diff --check` passed. The host repository's `agent` remote was registered using the same production command; its existing origin was preserved.
- Code review covered direct transport, node execution and local bootstrap; its linked-worktree finding was reproduced and fixed.
- No real user container, external Git remote, or production credential was used by test fixtures.
