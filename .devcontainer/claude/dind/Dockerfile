FROM docker:dind-rootless@sha256:8a213afdd096a44dff403aaf8eb58b7a96a63113f18a4b094b98b7d0ed7d948b
# rootless dockerd runs as the agent's UID so that the agent can use its socket.
ARG ROOTLESS_UID=1000
ARG ROOTLESS_GID=1000
USER root
# rootlesskit maps the subordinate IDs 100000-165535 besides the user's own UID,
# so the two must not overlap. Alpine has no usermod.
RUN if [ "$ROOTLESS_UID" -le 0 ] || { [ "$ROOTLESS_UID" -ge 100000 ] && [ "$ROOTLESS_UID" -lt 165536 ]; }; then \
        echo "error: agent.uid = $ROOTLESS_UID must be positive and outside 100000-165535." >&2; exit 1; \
    fi \
    && sed -i "s/^rootless:x:1000:1000:/rootless:x:$ROOTLESS_UID:$ROOTLESS_GID:/" /etc/passwd \
    && sed -i "s/^rootless:x:1000:/rootless:x:$ROOTLESS_GID:/" /etc/group \
    && grep -q "^rootless:x:$ROOTLESS_UID:$ROOTLESS_GID:" /etc/passwd \
    && chown -R rootless:rootless /home/rootless
USER rootless
