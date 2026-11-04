#!/bin/bash
# commands.release_verify of this repo, run by /release after the GitHub Release with RELEASE_TAG set: a temporary
# clone of main upgraded to the tag must show no diff and no untracked file. The clone is removed on exit.
set -euo pipefail

repo=$(git rev-parse --show-toplevel)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
git clone --quiet --branch main "$repo" "$tmp/repo"
# npm fetches github: over HTTPS; a credential prompt can wait forever (#117). allow-comment: dependency
n=${GIT_CONFIG_COUNT:-0}
(cd "$tmp/repo" && env GIT_TERMINAL_PROMPT=0 \
  "GIT_CONFIG_KEY_$n=url.git@github.com:.insteadOf" "GIT_CONFIG_VALUE_$n=https://github.com/" \
  "GIT_CONFIG_KEY_$((n + 1))=credential.helper" "GIT_CONFIG_VALUE_$((n + 1))=" \
  GIT_CONFIG_COUNT=$((n + 2)) npx --yes "github:tayebchlyah/heldout-dev-loop#$RELEASE_TAG" upgrade)
git -C "$tmp/repo" diff --exit-code
test -z "$(git -C "$tmp/repo" status --porcelain)"
