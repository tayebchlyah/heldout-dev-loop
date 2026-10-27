#!/bin/bash
# commands.release_verify of this repo, run by /release after the GitHub Release with RELEASE_TAG set: a temporary
# clone of main upgraded to the tag must show no diff and no untracked file. The clone is removed on exit.
set -euo pipefail

repo=$(git rev-parse --show-toplevel)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
git clone --quiet --branch main "$repo" "$tmp/repo"
(cd "$tmp/repo" && npx --yes "github:tchlyah/dev-loop#$RELEASE_TAG" upgrade)
git -C "$tmp/repo" diff --exit-code
test -z "$(git -C "$tmp/repo" status --porcelain)"
