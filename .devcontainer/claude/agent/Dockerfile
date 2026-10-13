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
COPY agent/managed-settings.json /etc/claude-code/managed-settings.json
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
COPY --chmod=755 agent/entrypoint.sh agent/init-firewall.sh agent/wait-ready.sh agent/clone-workspace.py agent/workspace_mounts.py agent/nix-env.py /usr/local/bin/
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
