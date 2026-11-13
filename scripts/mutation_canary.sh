#!/bin/bash
# Usage: scripts/mutation_canary.sh <fixture folder>
# Exit 0: the report of `devloop.py mutation --run` equals expected.txt. Exit 1: it differs (a unified diff on stdout).
# The exit code of `mutation` itself is not read, only its report.
set -eu
fixture=$(cd "$1" && pwd)
root=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
repo=$work/repo
mkdir "$repo"
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
export GIT_AUTHOR_NAME=canary GIT_AUTHOR_EMAIL=canary@example.invalid GIT_AUTHOR_DATE=2000-01-01T00:00:00Z
export GIT_COMMITTER_NAME=canary GIT_COMMITTER_EMAIL=canary@example.invalid GIT_COMMITTER_DATE=2000-01-01T00:00:00Z
git -C "$repo" init -q -b main
cp -R "$fixture/base/." "$repo/"
git -C "$repo" add -A
git -C "$repo" commit -q -m base
base=$(git -C "$repo" rev-parse HEAD)
cp -R "$fixture/head/." "$repo/"
git -C "$repo" add -A
git -C "$repo" commit -q -m head
if [ -f "$fixture/prepare.sh" ]; then
  (cd "$repo" && bash "$fixture/prepare.sh")
fi
python3 "$root/skill/scripts/devloop.py" mutation --repo "$repo" --head main --base "$base" --run >"$work/out.json" || true
python3 - "$work/out.json" >"$work/report" <<'EOF'
import json, re, sys
report = json.load(open(sys.argv[1]))["report"]
print(re.sub(r"(?m)^(base|head): \S+$", r"\1: <\1>", report))
EOF
diff -u "$fixture/expected.txt" "$work/report"
