FROM docker:dind-rootless@sha256:8a213afdd096a44dff403aaf8eb58b7a96a63113f18a4b094b98b7d0ed7d948b AS docker-cli
FROM node:22-bookworm-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates openssl git gh curl ripgrep python3 bubblewrap socat iptables \
    util-linux procps openjdk-17-jdk-headless gradle \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code@2.1.295 \
    && npm cache clean --force
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-cli /usr/local/libexec/docker/cli-plugins/ /usr/local/libexec/docker/cli-plugins/
COPY dind-bridge/src/ /opt/dind-bridge/
RUN chmod 755 /opt/dind-bridge/main.mjs \
    && ln -s /opt/dind-bridge/main.mjs /usr/local/bin/dind-bridge \
    && mkdir -p /etc/dind-bridge /etc/claude-code \
    && dind-bridge env-file --socket /run/dind-bridge/bridge.sock \
         --api tcp://127.0.0.1:2375 --publish-ip 0.0.0.0 \
         --node /usr/local/bin/node --script /usr/local/bin/dind-bridge \
         > /etc/dind-bridge/env.sh \
    && install -d -m 700 -o node -g node /run/dind-bridge \
    && install -d -o node -g node /workspace
COPY .devcontainer/managed-settings.json /etc/claude-code/managed-settings.json
COPY --chmod=755 .devcontainer/entrypoint.sh .devcontainer/init-firewall.sh /usr/local/bin/
ENV NODE_EXTRA_CA_CERTS=/usr/local/share/ca-certificates/managed-proxy.crt \
    GIT_SSL_CAINFO=/etc/ssl/certs/ca-certificates.crt \
    CURL_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    HOME=/home/node \
    SHELL=/bin/bash \
    CLAUDE_CODE_SHELL=/bin/bash
WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["sleep", "infinity"]
