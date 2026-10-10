#!/bin/bash
set -euo pipefail
curl --noproxy '*' --fail --max-time 5 http://127.0.0.1:2375/_ping | grep -qx OK
curl --noproxy '*' --fail --max-time 5 "http://127.0.0.1:$FIXTURE_PORT/" | grep -qx bridge-ok
# Attach and stdin go through the relay, not just plain API requests.
echo stdin-ok | docker run --rm -i busybox:1.37 cat | grep -qx stdin-ok
printf 'bridge-probe-ok\n'
