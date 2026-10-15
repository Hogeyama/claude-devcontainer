FROM mitmproxy/mitmproxy:11.1.3@sha256:e0deb0df7edf9f909053f274a067cd1cacb90f5c17d74459e1693179c0b98d8f
USER 1000:1000
ENV HOME=/home/mitmproxy
COPY proxy.py /opt/code-server-proxy.py
# websocket=false: after a 101 the stream is passed through as it is. VS Code's
# managed connection upgrades without WebSocket framing, which the WebSocket
# layer would reject; the allowlist has already decided the target by then.
ENTRYPOINT ["mitmdump", "--listen-port", "8080", "--set", "block_global=false", "--set", "connection_strategy=lazy", "--set", "websocket=false", "-s", "/opt/code-server-proxy.py"]
