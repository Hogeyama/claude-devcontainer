# The workspace's .devcontainer/ (compose.yaml passes it). Without it, as in
# `docker build proxy`, this empty stage stands in.
FROM scratch AS workspace-config
FROM scratch AS workspace-proxy
COPY --from=workspace-config proxy.yam[l] proxy.local.yam[l] /
FROM mitmproxy/mitmproxy:11.1.3@sha256:e0deb0df7edf9f909053f274a067cd1cacb90f5c17d74459e1693179c0b98d8f
USER root
RUN mkdir -p /opt/proxy /public-ca /home/mitmproxy/.mitmproxy \
    && chown mitmproxy:mitmproxy /public-ca /home/mitmproxy/.mitmproxy
COPY addon.py entrypoint.py credentials.py workspace_config.py /opt/proxy/
# The workspace's proxy.yaml and this host's proxy.local.yaml, checked here
# so that a bad file fails the build.
RUN --mount=type=bind,from=workspace-proxy,target=/tmp/workspace-config \
    python /opt/proxy/workspace_config.py install /tmp/workspace-config
USER mitmproxy
# Container stdout is a pipe; without this, denials reach `docker logs` only in large batches.
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
ENTRYPOINT ["python", "/opt/proxy/entrypoint.py"]
