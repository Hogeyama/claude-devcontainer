The proxy policy is defined in `addon.py`, with host settings for workspace
hosts, connectors and web search. Rebuild the image from the host to change
the code; never mount an agent-editable addon into the running proxy.

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
workspace-config=<.devcontainer> proxy`; the workspace's `.generated/proxy.json`
there (`proxy.hosts` of `claude.toml`, merged by `scripts/settings.py`) is
checked and baked in, and the build fails without it. The proxy
starts without credentials. `python /opt/proxy/credentials.py inject` reads a
JSON snapshot with `anthropic_token`, `auth_mode` (`api-key` or `oauth`) and
optionally `secrets` (`{NAME: value}` for proxy.hosts' `${NAME}`) from stdin and stores it in `/run/proxy-credentials`, which must be a
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

`proxy.web_search = true` in `claude.toml` or `claude.local.toml` opts in to
`web_search_20250305`, the basic search used by Claude Code. It defaults to
false. Host initialization passes it as `CLAUDE_DEVCONTAINER_WEB_SEARCH=1` to
the proxy only; restart the environment after changing it. No image rebuild
is needed to toggle the setting once this code is installed. Later search
versions, including ones that run code, remain blocked. Search traffic from
Anthropic is outside `proxy.hosts`; the proxy cannot inspect search queries
or result sources. This permission also applies to direct API calls from Bash.

claude.ai connectors (mcp-proxy.anthropic.com) are closed unless their IDs are
listed in `proxy.connectors` of `claude.toml`; listed ones get the claude.ai OAuth
token, and what they do on the connected account is not inspected.

The Artifact tool reads a page's files from `<slug>.frame.claudeusercontent.com`,
one subdomain per artifact, so `addon.py` matches that host by suffix: `GET` of
`/_f/...` and of the `/_warmup` probe. The read token is in the query, and the
proxy adds no credentials.

Other hosts come only from the workspace's `proxy.hosts`: its routes, the
headers the agent may pass, and the headers injected with the host's secrets,
which replace the agent's credentials. `python /opt/proxy/workspace_config.py
secrets` lists the secret names, which the host injector asks for. The
template's GitHub setting injects the PAT as Basic authentication; GraphQL
bodies are opaque, so the PAT's scope must enforce repository permissions.
Request bodies are buffered without an application size limit; responses stream without a cap. Deploy a memory limit for the proxy to
contain request-buffer exhaustion. WebSockets, plaintext upstream HTTP, and
non-443 upstream ports are rejected. Denials are logged with method, host,
path and the check that failed, but never the query or body content.
