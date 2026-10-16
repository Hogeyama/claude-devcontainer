---
name: claude-devcontainer
description: Use in this dev container before handing commits to the host, checking what the host has accepted, or comparing your files or Git with the host's, and when clone, Git, network requests, package installs, Docker or Testcontainers fail, or when a task needs unavailable tools or dependencies. Covers the independent workspace, host review and mandatory proxy.
---

# Working in this dev container

You run in an isolated container. Most of its limits are deliberate, and working around them would break the isolation that lets a human run you unattended. When something is impossible here, do not look for a way around it; tell the human what change to make and why.

## Network

All traffic goes through the proxy in `HTTPS_PROXY`. Only these destinations are allowed:

- `api.anthropic.com`: Claude's own traffic only. Do not call it from commands: the proxy adds the real credentials to every request.
- `<slug>.frame.claudeusercontent.com`: the Artifact tool's reads of a page (`GET`) only.
- What `.devcontainer/claude.toml` (and the host's `claude.local.toml`) lists under `[proxy.hosts."..."]`, usually `github.com` (Git smart HTTP: clone, fetch, push) and `api.github.com` (the REST and GraphQL APIs for repositories). `gh` works. The proxy adds the credentials named in its `inject`, so leave dummy tokens such as `GH_TOKEN=managed-by-proxy` as they are.

Package registries (npm, PyPI, Maven Central, crates.io, ...), GitHub release assets, LFS and archives, and every other website are unreachable. The proxy blocks WebSearch by default; the host can opt in with `[proxy] web_search = true` in `claude.toml` or `claude.local.toml` and restart the environment. This allows server-side search, not direct access to result pages.

- **HTTP 403**: the proxy refused the request. Check that the destination is one of the above. A body of `blocked: no credentials` means the proxy has no credentials yet; the human must run `python3 .devcontainer/claude/scripts/inject-credentials.py` on the host. `blocked: no secret NAME` means the host's credentials file lacks the secret that `proxy.hosts` injects; tell the human the NAME.
- **Timeout or `Could not resolve host`**: the program did not use the proxy. Tools that ignore `HTTPS_PROXY` cannot reach anything.
- **Certificate errors**: the proxy inspects HTTPS and signs with its own CA. The system trust store and the Node.js, Git, and curl variables already include it. For a tool with its own trust store (a Java keystore, Python's `certifi`), add `/public-ca/mitmproxy-ca-cert.pem` to it. Do not disable TLS verification.

When a dependency cannot be downloaded, do not keep retrying or try mirrors. Report it and suggest how the human can provide it (see "Adding tools and dependencies").

## Files and Git

Check the Git mode first: `echo "$CLAUDE_DEVCONTAINER_GIT_ISOLATION"` prints `clone`, `shared` or `unsafe_shared`, the mode the container was created with. Do not infer it from `claude.toml`: the host's `claude.local.toml` can override it, and a change applies only after the container is recreated. Follow the rules for both modes and the subsection for your mode only; the other mode's paths, remotes and exchange steps do not work for you.

In both modes:

- Commit, then tell the human the branch name and the exact commit ID. The human fetches it through the host's `agent` remote, reviews it, and integrates that ID. GitHub is not involved in this exchange.
- If Git has no `user.name`/`user.email`, ask the human which identity to use. They can provide one with `~/.config/claude-devcontainer/extra/.gitconfig` on the host, which becomes your `~/.gitconfig`. Do not invent one or write global config without asking.
- `/run/host/generated` and `/run/host/nix-env` are read-only management inputs. Mount changes require container recreation. Run Git as node, never root.
- If `$CLAUDE_DEVCONTAINER_NIX` is `1`, Bash reads what the nix dev shell exported when the container started, after its shellHook ran in `/workspace`. When a dev shell tool or variable is missing, the shellHook's output is in `/run/claude-devcontainer/nix-env.log`.
- If `$CLAUDE_DEVCONTAINER_SUMI` is `1`, secrets that the human listed are replaced by `*` in what your tools return, one `*` per byte (a 24-byte secret reads as 24 `*`). This is deliberate, and a run of `*` is the masking working, not a change to report. Do not try to recover the values (decoding, splitting, or guessing them); if a task needs one, ask the human. A tool that fails with a sumi error means its output could not be masked; tell the human.

### `unsafe_shared`

The working tree AND Git index, HEAD, refs and objects are the host's. Commits immediately affect the host; do not use the agent remote or the shared-mode fetch/reset workflow. Startup preserves the host index. Coordinate Git operations with the host. Git configuration is read-only; hooks, info and objects/info are masked. .devcontainer and configured secret masks remain protected. Hidden tracked paths can appear deleted or empty; never stage them with a blanket add. Index flags and history changes affect the host too. Rebase state (including rebase-todo), rebase-apply and sequencer remain writable and shared; continuing a rebase on the host also executes its todo exec commands there.

### `shared`

`/workspace` is the host working tree: ordinary edits reach the host immediately. `/workspace/.git` is this container's own Git, so add and commit update only it. The host's `.git` is read-only at `/run/host/git`. `.devcontainer` is read-only; use clone to develop it. `git.shared_hidden_paths` and in-repository `sumi.secrets` are covered by empty read-only mounts. Other untracked/ignored files remain shared. Tracked files among the hidden paths carry the skip-worktree flag in this index (`git ls-files -v` shows `S`), so they stay out of status and commits even though they read as empty here; a clean status does not mean their content matches the host's.

- The human often talks to you from an editor on the host (ACP clients such as Avante or agent-shell, through `acp-agent.py`). They see the same files, but their `git status` compares the host's index with the real files.
- Work on the branch you started on, or on an `agent/<task>` branch created from it. Do not use switch, stash or checkout to replace the files of the working tree: they are the human's files, possibly being edited.
- The host integrates your commit with `git reset --mixed`, which moves its HEAD and index and keeps the files. Edits you leave uncommitted stay in the working tree and appear in the host's `git status`.
- When the host has new commits, run `git fetch /run/host/git <branch>` and then `git reset --mixed FETCH_HEAD` (the files are already the host's) or a rebase. `git push agent` is not available in this mode. At container start, HEAD follows the host's branch automatically when no commit here is still unaccepted.
- `/run/host/git` is the host's Git directory without a working tree. Read the host's state yourself with `git -C /run/host/git` (`log`, `rev-parse HEAD`, `reflog`, `branch`) instead of asking the human to run commands and paste output. To check that the host accepted commit `<id>`: `git -C /run/host/git merge-base --is-ancestor <id> HEAD`.
- Commands that need a working tree (`status`, `diff` without two commits) fail there; that is expected. Do not pair it with `/workspace` (`--work-tree=/workspace`, `GIT_DIR=/run/host/git`): the hidden paths read as empty here, so the comparison reports deletions the host does not have. To compare with the host, compare commits (`git diff <host commit> <your commit>`).
- `/run/host/git` is read-only; never try to write there or to reconfigure Git around it.
- Do not create a `.git` directory or file anywhere below `/workspace`: Git run by the host inside that directory would use its config and hooks.

### `clone`

- `/workspace` is your own persistent clone, initialized automatically from a local copy of the host's committed HEAD (including unpublished commits), using the host's branch name at creation time. If the host has detached HEAD, the clone does too. Later starts preserve your current branch. Create a separate task branch with `git switch -c agent/task-name` when needed.
- The host's worktree and `.git` are not mounted, and `/run/host/git` does not exist. Your `.devcontainer/`, Git configuration and hooks are writable; host startup uses the host's copies until the human reviews and integrates your commit.
- Commit work on a task branch. No push is required: the host's `agent` remote points directly to this container and fetches your commits with ordinary Git.
- You have no automatic remote that can write back to the host. The human fetches, reviews fixed commit IDs, then integrates that ID.
- To receive host changes, ask the human to run `git push agent HEAD:refs/heads/host/main` on the host, then merge/rebase the local `host/main` branch here. For host-only tests, commit the code and ask for review before execution; receive results in chat.
- The human may also push, even with `--force`, to the branch checked out here. Git then updates the work tree and index to the pushed commit, and refuses the push while either has uncommitted changes. Commits a forced push removes from the branch stay in `git reflog`.
- Restarts and container recreation preserve your clone, dirty files and dependency changes. They do not automatically fetch/reset. Never recommend deleting workspace volumes as routine recovery.

## Docker (DinD)

Docker is available only if `$CLAUDE_DEVCONTAINER_DIND` is `1`. Otherwise `docker` cannot work, and the human has to set `enabled = true` under `[docker]` in `.devcontainer/claude.toml`.

- DinD reaches no registry and no external DNS. `docker pull` fails, and so does any network access during a build (`apt-get install`, `npm install`, ...).
- The only images are those the human listed in `images` under `[docker]` in `.devcontainer/claude.toml`; `docker images` shows them. If one is missing, give the human its exact `name:tag`.
- Build with `docker build --network=none`, from images already loaded and dependency files already on disk. Run Gradle with `--offline`.
- Publish ports on `0.0.0.0` (`-p 8080` or `-p 18080:8080`) and connect to `127.0.0.1:<published port>`. Ports published on loopback only (`-p 127.0.0.1:8080:8080`) and port 2375 are not relayed.
- `DOCKER_HOST` and the Testcontainers variables are already set. Testcontainers also needs the Ryuk image (e.g. `testcontainers/ryuk:0.14.0`) in that list.

## Adding tools and dependencies

Package registries are unavailable. In shared mode, ask the human to edit `.devcontainer` on the host, or switch to clone for this work. In clone mode, propose changes through a commit; the human reviews and integrates them into the host's `.devcontainer/`, then applies them with `devcontainer up --remove-existing-container`.

| What is needed | Change for the human to make |
|---|---|
| A command or language runtime | `.devcontainer/agent-setup.sh`, run as root when the image is built |
| An image for DinD | `images` under `[docker]` in `.devcontainer/claude.toml` |
| Another host or API | `.devcontainer/claude.toml`, a `[proxy.hosts."api.example.com"]` table (the host name quoted) with the narrowest `routes` (`"GET /path"` or a bare path for any method). For a token, `inject` (e.g. `inject = { Authorization = "Bearer ${NOTION_API_KEY}" }`), `env = ["NOTION_API_KEY"]` for the dummy the tool reads, and the human's `NOTION_API_KEY=...` in `~/.config/claude-devcontainer/credentials`. A host only this human uses goes in `.devcontainer/claude.local.toml` instead |
| The nix dev shell of the repository's flake | `enabled = true` under `[nix]` in `.devcontainer/claude.toml` (already on if `$CLAUDE_DEVCONTAINER_NIX` is `1`) |
| Environment variables or volumes | `.devcontainer/compose.yaml` |
| A claude.ai connector (Google Drive, ...) | `connectors = ["<id>"]` under `[proxy]` in `.devcontainer/claude.local.toml`. List the IDs with `curl -s 'https://api.anthropic.com/v1/mcp_servers?limit=1000' -H 'anthropic-beta: mcp-servers-2025-12-04' \| jq -r '.data[] \| "\(.id)\t\(.display_name)"'` and give the human the ID of the one needed. It needs the host's Claude login; with an API key there are no connectors |

Root `node_modules` reuses host dependencies through CoW. Your changes go to a persistent upper layer; they do not update host dependencies or the pnpm store. Additional monorepo mounts need host configuration. npm, ordinary pnpm and Yarn node_modules mode are supported; Yarn PnP and external virtual stores are not automatic. Native addons require compatible Node/libc.

Ask the human to stop/remove containers before changing host dependencies, then preserve needed agent edits and refresh only the Node upper/work directories. Do not clear the workspace volume or Gradle state. Gradle uses its existing CoW cache with `--offline`; do not add registry routes or Java proxy settings to compensate for missing dependencies.
