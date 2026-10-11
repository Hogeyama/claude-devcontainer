# The workspace's .devcontainer/ (compose.yaml passes it). Without it, as in
# `docker build proxy`, this empty stage stands in and the build fails.
FROM scratch AS workspace-config
FROM mitmproxy/mitmproxy:11.1.3@sha256:e0deb0df7edf9f909053f274a067cd1cacb90f5c17d74459e1693179c0b98d8f
USER root
RUN mkdir -p /opt/proxy /public-ca /home/mitmproxy/.mitmproxy \
    && chown mitmproxy:mitmproxy /public-ca /home/mitmproxy/.mitmproxy
COPY addon.py entrypoint.py credentials.py policy.py workspace_config.py /opt/proxy/
# proxy.hosts of the workspace's claude.toml and this host's claude.local.toml,
# merged by scripts/settings.py on the host and checked again here.
RUN --mount=type=bind,from=workspace-config,source=.generated/proxy.json,target=/tmp/proxy.json \
    python /opt/proxy/workspace_config.py install /tmp/proxy.json
USER mitmproxy
# Container stdout is a pipe; without this, denials reach `docker logs` only in large batches.
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
ENTRYPOINT ["python", "/opt/proxy/entrypoint.py"]
