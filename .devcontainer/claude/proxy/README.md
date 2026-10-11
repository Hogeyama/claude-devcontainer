The proxy policy is fixed in `addon.py`. Rebuild the image from the host to
change it; never mount an agent-editable addon into the running proxy.

Run unit tests with Python and `proxy/requirements.txt` installed:

```sh
python -m unittest discover -s proxy -p '*_test.py' -v
```

Or use the pinned image without installing Python dependencies:

```sh
docker run --rm -v "$PWD/proxy:/work/proxy:ro" -w /work \
  --entrypoint python \
  mitmproxy/mitmproxy:11.1.3@sha256:e0deb0df7edf9f909053f274a067cd1cacb90f5c17d74459e1693179c0b98d8f \
  -m unittest discover -s proxy -p '*_test.py' -v
```

Build using `docker build -t claude-devcontainer-proxy --build-context
workspace-config=<.devcontainer> proxy`; the workspace's `proxy.yaml` there is
checked and baked in, and without it only Anthropic is reachable. The proxy
starts without credentials. `python /opt/proxy/credentials.py inject` reads a
JSON snapshot with `anthropic_token`, `auth_mode` (`api-key` or `oauth`) and
optionally `secrets` (`{NAME: value}` for proxy.yaml's `${NAME}`) from stdin and stores it in `/run/proxy-credentials`, which must be a
private tmpfs; `scripts/inject-credentials.py` sends it from the host. Requests
are denied while the stored snapshot is missing or malformed. The image runs as the mitmproxy user (UID 1000).
The private CA stays in `/home/mitmproxy/.mitmproxy`; only its certificate is
exported to `/public-ca/mitmproxy-ca-cert.pem`.

Anthropic requests need no Content-Encoding, so that Messages/token-count
bodies can be buffered and inspected as sent. The inspection rejects only the
ways a request can make Anthropic's servers contact other hosts: `mcp_servers`,
`container`, server tools (any tool whose type is not `custom`, except the
advisor_20260301, which only runs a model; later versions until checked), and URL or
file sources in messages, the system prompt, and tool results. Other fields
and beta headers pass, so new Claude Code features need no policy update; a
future server-side feature in some other shape would pass too.

claude.ai connectors (mcp-proxy.anthropic.com) are closed unless their IDs are
listed in CLAUDE_DEVCONTAINER_CONNECTORS; listed ones get the claude.ai OAuth
token, and what they do on the connected account is not inspected.

Other hosts come only from the workspace's proxy.yaml: its routes, the
headers the agent may pass, and the headers injected with the host's secrets,
which replace the agent's credentials. `python /opt/proxy/workspace_config.py
secrets` lists the secret names, which the host injector asks for. The
template's GitHub setting injects the PAT as Basic authentication; GraphQL
bodies are opaque, so the PAT's scope must enforce repository permissions.
Request bodies are buffered without an application size limit; responses stream without a cap. Deploy a memory limit for the proxy to
contain request-buffer exhaustion. WebSockets, plaintext upstream HTTP, and
non-443 upstream ports are rejected. Denials are logged with method, host,
path and the check that failed, but never the query or body content.
