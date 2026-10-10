#!/bin/sh
# Rebuild the dist branch from the first-parent history of a source branch:
# one commit for each source commit that changed .devcontainer/claude, holding
# that directory plus an UPSTREAM file with the URL of the source commit.
# UPSTREAM is a file, not a trailer, because consumers pull with --squash,
# which keeps the tree but not the messages.
#
# Each dist commit copies its source commit's author, committer, dates and
# message, so a rebuild reproduces the same hashes and dist only fast-forwards.
#
# Usage: scripts/build-dist.sh [source-ref [dist-branch]]
set -eu
source_ref=${1:-main}
dist=${2:-dist}
prefix=.devcontainer/claude
# The repository's web URL: from GitHub Actions, else from the origin remote.
if [ -n "${GITHUB_REPOSITORY:-}" ]; then
    url=${GITHUB_SERVER_URL:-https://github.com}/$GITHUB_REPOSITORY
else
    url=$(git remote get-url origin | sed -E 's#^(ssh://)?git@([^:/]+)[:/]#https://\2/#; s#\.git$##')
fi

field() { git log -1 --format="$1" --date=raw "$commit"; }

parent=
last=
for commit in $(git rev-list --first-parent --reverse "$source_ref"); do
    core=$(git rev-parse -q --verify "$commit:$prefix") || continue
    [ "$core" = "$last" ] && continue
    last=$core
    upstream=$(echo "$url/commit/$commit" | git hash-object -w --stdin)
    tree=$({ git ls-tree "$core"; printf '100644 blob %s\tUPSTREAM\n' "$upstream"; } | git mktree)
    parent=$(
        GIT_AUTHOR_NAME=$(field %an) GIT_AUTHOR_EMAIL=$(field %ae) GIT_AUTHOR_DATE=$(field %ad) \
        GIT_COMMITTER_NAME=$(field %cn) GIT_COMMITTER_EMAIL=$(field %ce) GIT_COMMITTER_DATE=$(field %cd) \
        git commit-tree "$tree" ${parent:+-p "$parent"} -m "$(field %B)")
done
if [ -z "$parent" ]; then
    echo "error: no commit in $source_ref has $prefix" >&2
    exit 1
fi
git update-ref "refs/heads/$dist" "$parent"
echo "$dist -> $parent"
