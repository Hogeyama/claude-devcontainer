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
# The workspace's owner on the host (CLAUDE_DEVCONTAINER_UID/GID in compose.yaml).
ARG AGENT_UID=1000
ARG AGENT_GID=1000
RUN if [ "$AGENT_UID" -le 0 ]; then echo "error: CLAUDE_DEVCONTAINER_UID must be positive." >&2; exit 1; fi \
    && groupmod -o -g "$AGENT_GID" node \
    && usermod -o -u "$AGENT_UID" -g "$AGENT_GID" node
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-cli /usr/local/libexec/docker/cli-plugins/ /usr/local/libexec/docker/cli-plugins/
COPY dind-bridge/src/ /opt/dind-bridge/
RUN chmod 755 /opt/dind-bridge/main.mjs \
    && ln -s /opt/dind-bridge/main.mjs /usr/local/bin/dind-bridge \
    && mkdir -p /etc/claude-code \
    && install -d -m 700 -o node -g node /run/dind-bridge \
    && install -d -o node -g node /workspace \
    # The claude-projects volume copies this owner when it is first created.
    && install -d -o node -g node /home/node/.claude /home/node/.claude/projects \
    # .git/config is read-only (see compose.yaml); do not try to record upstreams.
    && git config --system branch.autoSetupMerge false \
    # Interactive shells, including Nix's Bash launched by VS Code.
    && printf '%s\n' '. /etc/bash-env.sh' >>/home/node/.bashrc
# The workspace adds its tools with .devcontainer/agent-setup.sh, run as root on
# the host's network before the proxy applies. Core files are copied after it.
RUN --mount=type=bind,from=workspace-setup,target=/tmp/workspace-config \
    if [ -f /tmp/workspace-config/agent-setup.sh ]; then sh -eu /tmp/workspace-config/agent-setup.sh; fi
COPY agent/managed-settings.json /etc/claude-code/managed-settings.json
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
COPY --chmod=755 agent/entrypoint.sh agent/init-firewall.sh agent/wait-ready.sh /usr/local/bin/
ENV NODE_EXTRA_CA_CERTS=/usr/local/share/ca-certificates/managed-proxy.crt \
    GIT_SSL_CAINFO=/etc/ssl/certs/ca-certificates.crt \
    CURL_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    HOME=/home/node \
    BASH_ENV=/etc/bash-env.sh \
    SHELL=/bin/bash \
    CLAUDE_CODE_SHELL=/bin/bash
WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["sleep", "infinity"]
