#!/bin/bash
# commands.release_check of this repo, run by /release with RELEASE_VERSION and RELEASE_TAG set: the live canary,
# then, for a final version, promoted --sha (the commit ships the kit source of its last rc).
set -euo pipefail

scripts/canary.sh
if [[ "$RELEASE_VERSION" != *-rc.* ]]; then
  python3 scripts/repo_rules.py promoted --tag "$RELEASE_TAG" --sha HEAD
fi
