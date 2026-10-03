#!/bin/bash
# Prints one hash of the working tree: HEAD, every tracked change and every
# untracked file that is not ignored, by name and content. Shared by gate.sh
# and gate-snapshot.sh, so a subagent that wrote nothing compares equal.
{
  git rev-parse --verify --quiet HEAD || true
  git diff HEAD --binary --no-ext-diff 2>/dev/null || true
  git ls-files --others --exclude-standard -z 2>/dev/null | while IFS= read -r -d '' path; do
    printf '%s %s\n' "$path" "$(git hash-object -- "$path" 2>/dev/null || echo unreadable)"
  done
} | git hash-object --stdin
