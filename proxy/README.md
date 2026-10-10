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

Build using `docker build -t claude-devcontainer-proxy proxy`. The proxy starts
without credentials. `python /opt/proxy/credentials.py inject` reads a JSON
snapshot with `github_token`, `anthropic_token` and `auth_mode` (`api-key` or
`oauth`) from stdin and stores it in `/run/proxy-credentials`, which must be a
private tmpfs; `scripts/inject-credentials.py` sends it from the host. Requests
are denied while the stored snapshot is missing or malformed. The image runs as the mitmproxy user (UID 1000).
The private CA stays in `/home/mitmproxy/.mitmproxy`; only its certificate is
exported to `/public-ca/mitmproxy-ca-cert.pem`.

Messages/token-count requests need a Content-Length of at most 8 MiB and no
Content-Encoding, so that the body can be buffered and inspected; chunked
requests are closed before buffering. The inspection rejects only the ways a
request can make Anthropic's servers contact other hosts: `mcp_servers`,
`container`, server tools (any tool whose type is not `custom`), and URL or
file sources in messages, the system prompt, and tool results. Other fields
and beta headers pass, so new Claude Code features need no policy update; a
future server-side feature in some other shape would pass too.

GitHub GraphQL bodies are opaque, including invalid syntax. GitHub's scoped
PAT must enforce repository permissions. REST is limited to `/repos/...`,
`GET /user`, and `POST /graphql`; Git smart HTTP uses `.git` repository URLs.
Archive, LFS, release download hosts, and other endpoints are not supported.
GitHub request bodies are buffered without an application size limit;
responses stream without an 8 MiB cap. Deploy a memory limit for the proxy to
contain request-buffer exhaustion. WebSockets, plaintext upstream HTTP, and
non-443 upstream ports are rejected. Denials are logged with method, host and
path, but never the query.
