#!/bin/sh
# Extend the dist branch with the current .devcontainer/claude of a source
# branch, plus an UPSTREAM file with the URL of the source commit that last
# changed it. UPSTREAM is a file, not a trailer, because consumers pull with
# --squash, which keeps the tree but not the messages.
#
# dist only grows: `git subtree pull --squash` merges against the dist commit
# a consumer last pulled, so rewriting dist would break their next pull. The
# source branch, in contrast, may be force-pushed; a new commit is added when
# the core or the source commit behind it changed.
#
# Usage: scripts/build-dist.sh [source-ref [base-ref]]
#   base-ref: the published dist to extend (default: origin/dist, if any).
# Sets the local dist branch to the result.
set -eu
source_ref=${1:-main}
base=${2:-origin/dist}
prefix=.devcontainer/claude
# The repository's web URL: from GitHub Actions, else from the origin remote.
if [ -n "${GITHUB_REPOSITORY:-}" ]; then
    url=${GITHUB_SERVER_URL:-https://github.com}/$GITHUB_REPOSITORY
else
    url=$(git remote get-url origin | sed -E 's#^(ssh://)?git@([^:/]+)[:/]#https://\2/#; s#\.git$##')
fi

commit=$(git log -1 --first-parent --format=%H "$source_ref" -- "$prefix")
if [ -z "$commit" ]; then
    echo "error: no commit in $source_ref has $prefix" >&2
    exit 1
fi
field() { git log -1 --format="$1" --date=raw "$commit"; }

upstream=$(echo "$url/commit/$commit" | git hash-object -w --stdin)
tree=$({ git ls-tree "$commit:$prefix"; printf '100644 blob %s\tUPSTREAM\n' "$upstream"; } | git mktree)
parent=$(git rev-parse -q --verify "$base^{commit}" || true)
if [ -n "$parent" ] && [ "$(git rev-parse "$parent^{tree}")" = "$tree" ]; then
    git update-ref refs/heads/dist "$parent"
    echo "dist is up to date at $parent"
    exit 0
fi
dist=$(
    GIT_AUTHOR_NAME=$(field %an) GIT_AUTHOR_EMAIL=$(field %ae) GIT_AUTHOR_DATE=$(field %ad) \
    GIT_COMMITTER_NAME=$(field %cn) GIT_COMMITTER_EMAIL=$(field %ce) GIT_COMMITTER_DATE=$(field %cd) \
    git commit-tree "$tree" ${parent:+-p "$parent"} -m "$(field %B)")
git update-ref refs/heads/dist "$dist"
echo "dist -> $dist"
