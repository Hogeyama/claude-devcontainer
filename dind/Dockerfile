FROM docker:dind-rootless@sha256:8a213afdd096a44dff403aaf8eb58b7a96a63113f18a4b094b98b7d0ed7d948b
# rootless dockerd's user: the agent's UID, or with docker.map_agent_ids a UID
# of its own, while its containers' AGENT_UID and AGENT_GID are the agent's
# (scripts/settings.py dind_ids).
ARG ROOTLESS_UID=1000
ARG ROOTLESS_GID=1000
ARG AGENT_UID=1000
ARG AGENT_GID=1000
ARG MAP_AGENT_IDS=0
USER root
COPY subids.sh /tmp/subids.sh
# rootlesskit maps the subordinate IDs 100000-165535 besides the user's own UID,
# so the two must not overlap. Alpine has no usermod.
RUN if [ "$ROOTLESS_UID" -le 0 ] || { [ "$ROOTLESS_UID" -ge 100000 ] && [ "$ROOTLESS_UID" -lt 165536 ]; }; then \
        echo "error: agent.uid = $ROOTLESS_UID must be positive and outside 100000-165535." >&2; exit 1; \
    fi \
    && sed -i "s/^rootless:x:1000:1000:/rootless:x:$ROOTLESS_UID:$ROOTLESS_GID:/" /etc/passwd \
    && sed -i "s/^rootless:x:1000:/rootless:x:$ROOTLESS_GID:/" /etc/group \
    && grep -q "^rootless:x:$ROOTLESS_UID:$ROOTLESS_GID:" /etc/passwd \
    && chown -R rootless:rootless /home/rootless \
    && if [ "$MAP_AGENT_IDS" = 1 ]; then \
        sh /tmp/subids.sh "$AGENT_UID" > /etc/subuid \
        && sh /tmp/subids.sh "$AGENT_GID" > /etc/subgid \
        # dockerd gives its socket to the agent's group (--group), which it
        # must be in.
        && group=$(awk -F: -v gid="$AGENT_GID" '$3 == gid { print $1; exit }' /etc/group) \
        && if [ -z "$group" ]; then group=agent && addgroup -g "$AGENT_GID" "$group"; fi \
        && addgroup rootless "$group"; \
    fi \
    && rm /tmp/subids.sh \
    # The agent's /workspace is mounted here too. In clone mode it is a volume,
    # and DinD starts first: a new volume takes this directory's owner, the agent's.
    && install -d -o "$AGENT_UID" -g "$AGENT_GID" /workspace
USER rootless
