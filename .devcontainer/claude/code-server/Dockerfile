FROM mitmproxy/mitmproxy:11.1.3@sha256:e0deb0df7edf9f909053f274a067cd1cacb90f5c17d74459e1693179c0b98d8f
USER 1000:1000
ENV HOME=/home/mitmproxy
COPY proxy.py /opt/code-server-proxy.py
# websocket=false: after a 101 the stream is passed through as it is; the
# allowlist has decided the target by then, and the frames need no reading.
ENTRYPOINT ["mitmdump", "--listen-port", "8080", "--set", "block_global=false", "--set", "connection_strategy=lazy", "--set", "websocket=false", "-s", "/opt/code-server-proxy.py"]
