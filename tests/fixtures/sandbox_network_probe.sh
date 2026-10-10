#!/bin/bash
# Run by Claude's Bash tool: GitHub goes through the proxy, Anthropic never leaves the sandbox.
set -euo pipefail
github=$(curl --max-time 5 -s -o /dev/null -w '%{http_code}' https://api.github.com/user || true)
# The devcontainer proxy accepts this CONNECT, so a 403 here is the sandbox's own proxy.
anthropic=$(curl --max-time 5 -s -o /dev/null -w '%{http_connect} %{http_code}' https://api.anthropic.com/api/hello || true)
printf 'github=%s anthropic=%s\n' "$github" "$anthropic"
test "$github" = 200
test "${anthropic%% *}" = 403
printf 'sandbox-network-probe-ok\n'
