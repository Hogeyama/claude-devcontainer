---
name: claude-devcontainer
description: Limits of the sandboxed dev container you are running in (all egress through an inspecting proxy, .devcontainer/ and .git/config read-only, Docker only as an offline DinD) and what to do instead of working around them. Use it whenever a network request, package install, or download fails (HTTP 403, `blocked`, timeouts, `Could not resolve host`, `certificate verify failed`, `self-signed certificate in certificate chain`), when using docker, docker build, Testcontainers, or published ports, when `git push -u`, `git remote add`, or `git config` fails, when a write under .devcontainer/ or .git/ fails with `Read-only file system`, and when a task needs a tool, language runtime, or container image that is not installed.
---

# Working in this dev container

You run in an isolated container. Most of its limits are deliberate, and working around them would break the isolation that lets a human run you unattended. When something is impossible here, do not look for a way around it; tell the human what change to make and why.

## Network

All traffic goes through the proxy in `HTTPS_PROXY`. Only these destinations are allowed:

- `github.com`: Git smart HTTP (clone, fetch, push)
- `api.github.com`: the REST and GraphQL APIs for repositories. `gh` works; the proxy adds the credential, so leave the dummy `GH_TOKEN` as it is.
- `api.anthropic.com`: Claude's own traffic only. Bash commands cannot reach it.

The Bash sandbox allows only the two GitHub hosts, and a command that names extra allowed hosts is refused, so do not name any.

Package registries (npm, PyPI, Maven Central, crates.io, ...), GitHub release assets, LFS and archives, and every other website are unreachable. WebSearch is disabled.

- **HTTP 403**: the proxy refused the request. Check that the destination is one of the above. A body of `blocked: no credentials` means the proxy has no credentials yet; the human must run `python3 .devcontainer/claude/scripts/inject-credentials.py` on the host.
- **Timeout or `Could not resolve host`**: the program did not use the proxy. Tools that ignore `HTTPS_PROXY` cannot reach anything.
- **Certificate errors**: the proxy inspects HTTPS and signs with its own CA. The system trust store and the Node.js, Git, and curl variables already include it. For a tool with its own trust store (a Java keystore, Python's `certifi`), add `/public-ca/mitmproxy-ca-cert.pem` to it. Do not disable TLS verification.

When a dependency cannot be downloaded, do not keep retrying or try mirrors. Report it and suggest how the human can provide it (see "Adding tools and dependencies").

## Files and Git

- `.devcontainer/` is read-only, because the host builds and runs what is in it the next time it starts the container. When it needs a change, show the change and ask the human to make it. Do not get the change in through Git either (committing or staging a `.devcontainer/` change with `git update-index`, `git commit`, and the like).
- `.git/config`, `.git/hooks/`, and `.git/modules/` are read-only, for the same reason.
  - `git push -u` fails. Name the destination instead: `git push origin HEAD` or `git push origin <branch>`.
  - `git remote add`, repository-level `git config`, new hooks, and adding or updating submodules are impossible.
- Committing, creating branches, and pushing work.
- The working directory `/workspace` is the repository root. `git worktree` is not supported.

## Running commands

The Bash tool runs commands in a sandbox, and running them outside it is not allowed. Asking to disable the sandbox will not work.

## Docker (DinD)

Docker is available only if `$CLAUDE_DEVCONTAINER_DIND` is `1`. Otherwise `docker` cannot work, and the human has to set `CLAUDE_DEVCONTAINER_DIND=1` in `.devcontainer/.env`.

- DinD reaches no registry and no external DNS. `docker pull` fails, and so does any network access during a build (`apt-get install`, `npm install`, ...).
- The only images are those the human listed in `.devcontainer/images.txt`; `docker images` shows them. If one is missing, give the human its exact `name:tag`.
- Build with `docker build --network=none`, from images already loaded and dependency files already on disk. Run Gradle with `--offline`.
- Publish ports on `0.0.0.0` (`-p 8080` or `-p 18080:8080`) and connect to `127.0.0.1:<published port>`. Ports published on loopback only (`-p 127.0.0.1:8080:8080`) and port 2375 are not relayed.
- `DOCKER_HOST` and the Testcontainers variables are already set. Testcontainers also needs the Ryuk image (e.g. `testcontainers/ryuk:0.14.0`) in `images.txt`.

## Adding tools and dependencies

Nothing can be installed from a package registry while you run. The host provides these through the files below, all under the read-only `.devcontainer/`, and applies them with `devcontainer up --remove-existing-container`. Propose the exact addition to the human.

| What is needed | Change for the human to make |
|---|---|
| A command or language runtime | `.devcontainer/agent-setup.sh`, run as root when the image is built |
| An image for DinD | `.devcontainer/images.txt` |
| The nix dev shell of the repository's flake | `CLAUDE_DEVCONTAINER_NIX=1` in `.devcontainer/.env` (already on if `$CLAUDE_DEVCONTAINER_NIX` is `1`) |
| Environment variables or volumes | `.devcontainer/compose.yaml` |

Project dependencies that are already in the working tree, such as `node_modules`, can be used. If they are missing, ask the human to fetch them on the host.
