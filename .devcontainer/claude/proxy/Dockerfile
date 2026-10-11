# The workspace's .devcontainer/ (compose.yaml passes it). Without it, as in
# `docker build proxy`, this empty stage stands in.
FROM scratch AS workspace-config
FROM scratch AS workspace-routes
COPY --from=workspace-config proxy-routes.jso[n] /
FROM mitmproxy/mitmproxy:11.1.3@sha256:e0deb0df7edf9f909053f274a067cd1cacb90f5c17d74459e1693179c0b98d8f
USER root
RUN mkdir -p /opt/proxy /public-ca /home/mitmproxy/.mitmproxy \
    && chown mitmproxy:mitmproxy /public-ca /home/mitmproxy/.mitmproxy
COPY addon.py entrypoint.py credentials.py workspace_routes.py /opt/proxy/
# The workspace's extra routes, checked here so that a bad file fails the build.
COPY --from=workspace-routes / /opt/proxy/workspace/
RUN python /opt/proxy/workspace_routes.py
USER mitmproxy
# Container stdout is a pipe; without this, denials reach `docker logs` only in large batches.
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
ENTRYPOINT ["python", "/opt/proxy/entrypoint.py"]
