# tode.enabled in claude.toml: 1 installs terminal-code (tode) below.
ARG INSTALL_TODE=0
ARG INSTALL_CODE_SERVER=${INSTALL_TODE}
# sumi masks secrets in what Claude's tools return (sumi.enabled in
# claude.toml). Its broker and the agent must run the same version.
FROM scratch AS sumi-amd64
ADD --chmod=755 --checksum=sha256:43d89c297e8381adac12d2fcc6280bccd99f8386fddce35d13bb4e6aa687f5ed \
    https://github.com/Hogeyama/nix-agent-sandbox/releases/download/sumi-v0.5.0/sumi-x86_64-linux /sumi
FROM scratch AS sumi-arm64
ADD --chmod=755 --checksum=sha256:65f462ffa1831bb3e8b5f13198d5ef82d50d82bc0e6177dd824404d5bc3180f8 \
    https://github.com/Hogeyama/nix-agent-sandbox/releases/download/sumi-v0.5.0/sumi-aarch64-linux /sumi
FROM sumi-${TARGETARCH} AS sumi
# The sumi service: the broker, which alone reads the secrets file.
FROM scratch AS sumi-serve
COPY --from=sumi /sumi /sumi
ENTRYPOINT ["/sumi", "serve"]
# terminal-code (tode), VS Code drawn in the terminal through the kitty
# graphics protocol. Only the stage that INSTALL_TODE names is built, so the
# tarballs are downloaded only when tode.enabled is set. tode would otherwise
# fetch code-server from GitHub at its first start, which the proxy denies; the
# version and checksums are those tode pins (dist/codeserver/vendored.js).
FROM scratch AS tode-amd64
ADD --checksum=sha256:a8aae8c31c649781ee4fb3b174da08163830cc10e1dcee58465cc184a152cb25 \
    https://tode-releases.zenbu-labs.workers.dev/dl/stable/v0.4.2/tode-linux-x64.tar.gz /tode.tar.gz

FROM scratch AS tode-arm64
ADD --checksum=sha256:ab59e0aab3e1d171288699c3fbba508b5cf5f214f0c0db2ec42e35baf5396156 \
    https://tode-releases.zenbu-labs.workers.dev/dl/stable/v0.4.2/tode-linux-arm64.tar.gz /tode.tar.gz

FROM scratch AS code-server-amd64
ADD --checksum=sha256:a38d26f4cb81f768feddff79e2937fd3f39c83d3da8be3da7225e1087e62e4ed \
    https://github.com/coder/code-server/releases/download/v4.132.0/code-server-4.132.0-linux-amd64.tar.gz /code-server.tar.gz
FROM scratch AS code-server-arm64
ADD --checksum=sha256:ade569a677d1c04ee66ef153382b7e15bf261f955407663c7ddc6b87f9ee29fc \
    https://github.com/coder/code-server/releases/download/v4.132.0/code-server-4.132.0-linux-arm64.tar.gz /code-server.tar.gz
FROM tode-${TARGETARCH} AS tode-1
FROM scratch AS tode-0
FROM tode-${INSTALL_TODE} AS tode
FROM code-server-${TARGETARCH} AS code-server-1
FROM scratch AS code-server-0
FROM code-server-${INSTALL_CODE_SERVER} AS code-server
FROM docker:dind-rootless@sha256:8a213afdd096a44dff403aaf8eb58b7a96a63113f18a4b094b98b7d0ed7d948b AS docker-cli
# Only agent-setup.sh from the workspace's .devcontainer/, if there is one. A
# mount of the whole directory would rerun the setup whenever any file in it
# changed, including this image's own files under claude/.
FROM scratch AS workspace-setup
COPY --from=workspace-config agent-setup.s[h] /
FROM node:22-bookworm-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates openssl git gh curl jq ripgrep python3 iptables \
    util-linux procps \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code@2.1.295 \
    && npm cache clean --force
# The workspace's owner on the host (agent.uid and agent.gid in claude.toml).
ARG AGENT_UID=1000
ARG AGENT_GID=1000
RUN if [ "$AGENT_UID" -le 0 ]; then echo "error: agent.uid must be positive." >&2; exit 1; fi \
    && groupmod -o -g "$AGENT_GID" node \
    && usermod -o -u "$AGENT_UID" -g "$AGENT_GID" node
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-cli /usr/local/libexec/docker/cli-plugins/ /usr/local/libexec/docker/cli-plugins/
COPY dind-bridge/src/ /opt/dind-bridge/
RUN chmod 755 /opt/dind-bridge/main.mjs \
    && ln -s /opt/dind-bridge/main.mjs /usr/local/bin/dind-bridge \
    # The host's settings.json goes in managed-settings.d (scripts/copy-host-config.py).
    && mkdir -p /etc/claude-code/managed-settings.d \
    && install -d -m 700 -o node -g node /run/dind-bridge \
    && install -d -o node -g node /workspace \
    # The claude-projects volume copies this owner when it is first created.
    && install -d -o node -g node /home/node/.claude /home/node/.claude/projects \
    # The tode-user volume's mount point, made here whether or not tode is
    # installed: Docker would create missing parents, ~/.local among them, as root.
    && for dir in .local .local/share .local/share/tode .local/share/tode/vscode \
        .local/share/tode/vscode/user-data .local/share/tode/vscode/user-data/User; do \
      install -d -o node -g node "/home/node/$dir"; \
    done \
    # Interactive shells, including Nix's Bash launched by VS Code, and Bash
    # that takes itself for a command run by rshd, which reads ~/.bashrc in
    # place of BASH_ENV: one with a socket for stdin and no SHLVL, as Claude
    # Code's Bash tool is when VS Code starts Claude. At the top, since
    # Debian's ~/.bashrc returns early unless the shell is interactive.
    && sed -i '1i . /etc/bash-env.sh' /home/node/.bashrc
# The workspace adds its tools with .devcontainer/agent-setup.sh, run as root on
# the host's network before the proxy applies. Core files are copied after it.
RUN --mount=type=bind,from=workspace-setup,target=/tmp/workspace-config \
    if [ -f /tmp/workspace-config/agent-setup.sh ]; then sh -eu /tmp/workspace-config/agent-setup.sh; fi
# code-server is independently selectable; enabling it does not install Electron/tode.
RUN --mount=type=bind,from=code-server,target=/tmp/code-server \
    if [ -f /tmp/code-server/code-server.tar.gz ]; then \
      mkdir -p /opt/code-server \
      && tar -xzf /tmp/code-server/code-server.tar.gz -C /opt/code-server --strip-components 1 --no-same-owner \
      && chmod -R u-s,g-s,go-w /opt/code-server \
      && test -x /opt/code-server/bin/code-server; \
    fi
RUN install -d -o node -g node /home/node/.local/share/code-server \
    /home/node/.local/share/code-server/extensions /home/node/.local/share/code-server/user-data \
    /home/node/.local/share/code-server/user-data/User \
    /home/node/.local/share/code-server/user-data/Machine \
    /home/node/.vscode-server \
    && printf '%s\n' '{"terminal.integrated.profiles.linux":{"bash":{"path":"/bin/bash"}},"terminal.integrated.defaultProfile.linux":"bash"}' \
       > /home/node/.local/share/code-server/user-data/Machine/settings.json
# tode runs inside this container, Chromium and code-server included, and sends
# the terminal only pixels; see docs/usage.md. Chromium's own sandbox needs user
# namespaces or a setuid helper, which seccomp and no-new-privileges deny here,
# so tode's Electron runs with --no-sandbox: a page that broke out of a renderer
# would be a process of the agent's user in this container, which the agent
# already is. Headless Ozone keeps it off any display, forwarded or not.
# Docker gives /dev/shm 64 MiB, less than Chromium's frame buffers for a large
# screen, and a renderer that runs out dies without logging, which leaves the
# editor frozen; --disable-dev-shm-usage has Chromium use /tmp instead.
# The base image has no fonts but DejaVu, so Chromium drew Japanese and emoji
# as boxes. A Nix dev shell puts its Bash, built without readline, first on
# PATH, and that Bash prints the \[ \] of ~/.bashrc's prompt as they are, so
# code-server's terminal starts /bin/bash (code-server's Machine settings, which
# tode leaves alone); ~/.bashrc still loads the dev shell's environment.
RUN --mount=type=bind,from=tode,target=/tmp/tode \
    if [ -f /tmp/tode/tode.tar.gz ]; then \
      apt-get update && apt-get install -y --no-install-recommends \
        libnss3 libgtk-3-0 libasound2 libgbm1 \
        fonts-noto-core fonts-noto-cjk fonts-noto-color-emoji \
      && rm -rf /var/lib/apt/lists/* \
      && mkdir -p /opt/tode \
      && tar -xzf /tmp/tode/tode.tar.gz -C /opt/tode --strip-components 1 --no-same-owner \
      && chmod -R u-s,g-s,go-w /opt/tode \
      && electron=/opt/tode/node_modules/@zenbu-labs/pixel/electron/dist/pixel \
      && mv "$electron" "$electron.bin" \
      && if ldd "$electron.bin" | grep 'not found' >&2; then exit 1; fi \
      && printf '%s\n' '#!/bin/sh' \
        "[ -n \"\$ELECTRON_RUN_AS_NODE\" ] && exec $electron.bin \"\$@\"" \
        'unset DISPLAY WAYLAND_DISPLAY' \
        "exec $electron.bin \"\$@\" --no-sandbox --disable-dev-shm-usage --ozone-platform=headless '--screen-info={8192x8192}'" \
        > "$electron" \
      && chmod 755 "$electron" \
      && printf '%s\n' '#!/bin/sh' \
        'export ELECTRON_RUN_AS_NODE=1 TODE_INSTALL_ROOT=/opt/tode TODE_CODE_SERVER=/opt/code-server/bin/code-server TERMINAL_BROWSER_FRAMES=inline DO_NOT_TRACK=1' \
        "exec $electron.bin /opt/tode/dist/main.js \"\$@\"" \
        > /usr/local/bin/tode \
      && chmod 755 /usr/local/bin/tode \
      && machine=/home/node/.local/share/tode/vscode/user-data/Machine \
      && install -d -o node -g node "$machine" \
      && printf '%s\n' '{' \
        '  "terminal.integrated.profiles.linux": {"bash": {"path": "/bin/bash", "icon": "terminal-bash"}},' \
        '  "terminal.integrated.defaultProfile.linux": "bash"' \
        '}' > "$machine/settings.json" \
      && chown node:node "$machine/settings.json"; \
    fi
# tode.extensions in claude.toml, space-separated, from the Marketplace that
# tode sets for code-server. Installed here on the host's network, since the
# proxy does not let the agent reach the Marketplace and its CDN hosts.
ARG TODE_EXTENSIONS=""
RUN if [ -n "$TODE_EXTENSIONS" ]; then \
      data=/home/node/.local/share/tode/vscode \
      && install -d -o node -g node "$data/extensions" \
      && set -- \
      && for id in $TODE_EXTENSIONS; do set -- "$@" --install-extension "$id"; done \
      && HOME=/home/node EXTENSIONS_GALLERY='{"serviceUrl":"https://marketplace.visualstudio.com/_apis/public/gallery","itemUrl":"https://marketplace.visualstudio.com/items","cacheUrl":"https://vscode.blob.core.windows.net/gallery/index","controlUrl":""}' \
        runuser -u node -- /opt/code-server/bin/code-server \
        --extensions-dir "$data/extensions" --user-data-dir "$data/user-data" "$@" \
      && listed=$(HOME=/home/node runuser -u node -- /opt/code-server/bin/code-server \
        --extensions-dir "$data/extensions" --user-data-dir "$data/user-data" --list-extensions) \
      && for id in $TODE_EXTENSIONS; do \
        printf '%s\n' "$listed" | grep -qixF "${id%%@*}" || { echo "error: tode.extensions: $id was not installed" >&2; exit 1; }; \
      done; \
    fi
# codeServer.extensions in claude.toml, space-separated, each optionally
# @version. Installed like tode.extensions, at build time from the Marketplace
# (the agent has no runtime access to it), but into code-server's own
# directories. The build fails if an extension is not installed afterwards,
# compared as publisher.name, or as publisher.name@version when pinned.
ARG CODE_SERVER_EXTENSIONS=""
RUN if [ -n "$CODE_SERVER_EXTENSIONS" ]; then \
      data=/home/node/.local/share/code-server \
      && gallery='{"serviceUrl":"https://marketplace.visualstudio.com/_apis/public/gallery","itemUrl":"https://marketplace.visualstudio.com/items","cacheUrl":"https://vscode.blob.core.windows.net/gallery/index","controlUrl":""}' \
      && code_server() { \
        HOME=/home/node EXTENSIONS_GALLERY="$gallery" runuser -u node -- /opt/code-server/bin/code-server \
          --extensions-dir "$data/extensions" --user-data-dir "$data/user-data" "$@"; \
      } \
      && install -d -o node -g node "$data/extensions" \
      && set -- \
      && for id in $CODE_SERVER_EXTENSIONS; do set -- "$@" --install-extension "$id"; done \
      && code_server "$@" \
      && installed=$(code_server --list-extensions --show-versions) \
      && for id in $CODE_SERVER_EXTENSIONS; do \
        case "$id" in \
          *@*) listed="$installed" ;; \
          *) listed=$(printf '%s\n' "$installed" | cut -d@ -f1) ;; \
        esac \
        && if ! printf '%s\n' "$listed" | grep -qixF "$id"; then \
          echo "error: codeServer.extensions: $id was not installed" >&2; \
          exit 1; \
        fi; \
      done; \
    fi
# The entrypoint puts these hooks in managed-settings.d when the sumi service runs.
COPY --from=sumi /sumi /usr/local/bin/sumi
RUN sumi init --agent claude --server /run/sumi/mask.sock --shell /bin/bash --settings /tmp/sumi/settings.json \
    && install -D -m 644 /tmp/sumi/settings.json /usr/local/share/claude-devcontainer/sumi-settings.json \
    && rm -rf /tmp/sumi
# With an OAuth token, an unfinished onboarding makes the CLI check
# platform.claude.com, which the proxy does not allow, and refuse to start.
# /workspace is trusted up front: the container and the proxy are the
# boundary, so its hooks run confined either way.
COPY --chown=node:node --chmod=600 agent/claude.json /home/node/.claude.json
# Managed skills: only Claude in the container loads them (unlike .claude/skills
# in the workspace), the agent cannot change them, and copying the host's
# ~/.claude/skills does not replace them.
COPY agent/skills/ /etc/claude-code/.claude/skills/
COPY agent/bash-env.sh /etc/bash-env.sh
COPY --chmod=755 agent/entrypoint.sh agent/start-code-server.sh agent/init-firewall.sh agent/wait-ready.sh agent/clone-workspace.py agent/workspace_mounts.py agent/nix-env.py /usr/local/bin/
# Node's fetch ignores HTTPS_PROXY unless told; without the proxy it cannot
# reach anything (a Next.js server calling an API, for one).
ENV NODE_USE_ENV_PROXY=1
ENV NODE_EXTRA_CA_CERTS=/usr/local/share/ca-certificates/managed-proxy.crt \
    GIT_SSL_CAINFO=/etc/ssl/certs/ca-certificates.crt \
    CURL_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    HOME=/home/node \
    BASH_ENV=/etc/bash-env.sh \
    SHELL=/bin/bash \
    CLAUDE_CODE_SHELL=/bin/bash
# Dev Containers forwards the host's X11 display, and with it the host's screen
# and keyboard, into the container unless the container's environment has
# DISPLAY. So set it to a name that is no display, and blank it again in the
# processes that Dev Containers starts (VS Code's server and terminals,
# `devcontainer exec`), where Playwright, for one, takes any value for a display.
ENV DISPLAY=none
LABEL devcontainer.metadata='[{"remoteEnv":{"DISPLAY":""}}]'
WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["sleep", "infinity"]
