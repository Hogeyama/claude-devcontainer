# Isolated Clone Implementation Plan

The implemented architecture is specified in [the isolation design](../specs/2026-10-12-isolated-clone-design.md).

The initial version of this plan incorrectly used an external shared Git repository for bootstrap and handoff. The user clarified that both operations must stay local. Those steps are superseded by [local bootstrap and direct Git transport](2026-10-13-local-git-bootstrap.md).

Completed foundations retained from this plan:

- Agent workspace in a persistent Docker volume; host worktree and `.git` are not mounted.
- Read-only host management inputs for generated configuration and Nix environment.
- Root and nested dependency mount handling without recursive ownership changes.
- Existing Gradle CoW preserved; root `node_modules` CoW and explicit refresh lifecycle.
- Git runs as node after privilege drop.
- Reviewed integration uses fixed base/candidate commit IDs and `merge --ff-only`.
- Actual Docker, Claude, offline npm and Testcontainers validation.

See the current plan for bootstrap, remote registration, direct fetch/push and current validation. No real Gradle project build was performed in this environment.
