#!/bin/bash
# dev-loop gate for this repo: the installer suite (node) and the skill suite (python), side by side.
# GATE_SCOPE=changed (the Stop hook): any change reaches the node suite (it scans every tracked file),
# the skill suite only for skill/skills/claude/codex/bin/lib/tests/unit/python, AGENTS.md, package.json,
# the upstream watch. No change: neither. Else both.
# DEVLOOP_REPORT_DIR (devloop.py gate): JUnit XML of each suite there, and one FAILED line per failed test.
set -u
cd "$(dirname "$0")/.."

# core.fsmonitor in a user or repo config starts a daemon in each throwaway repo a test makes,
# and the daemon outlives the test. This override wins over every config file. It comes before
# the first git call and appends to the caller's own GIT_CONFIG_* entries.
GIT_CONFIG_N=${GIT_CONFIG_COUNT:-0}
export "GIT_CONFIG_KEY_$GIT_CONFIG_N=core.fsmonitor" "GIT_CONFIG_VALUE_$GIT_CONFIG_N=false" GIT_CONFIG_COUNT=$((GIT_CONFIG_N + 1))

node=1 py=1
if [ "${GATE_SCOPE:-}" = changed ]; then
  node=0 py=0
  while IFS= read -r path; do
    case "$path" in
      skill/*|skills/*|claude/*|codex/*|bin/*|lib/*|tests/unit/python/*|AGENTS.md|CODING_STANDARDS.md|package.json|upstream.json|scripts/upstream_watch.py) py=1 ;;
    esac
    node=1
  done < <(git diff --name-only HEAD; git ls-files --others --exclude-standard)
fi

# The node suite drives a real Chromium (drive.test.mjs). The gate installs nothing itself.
if [ $node = 1 ] && [ ! -d node_modules/playwright ]; then
  echo "run npm ci && npx playwright install chromium"
  exit 1
fi

out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT
report=()
[ -n "${DEVLOOP_REPORT_DIR:-}" ] && report=(--test-reporter=spec --test-reporter-destination=stdout
  --test-reporter=junit --test-reporter-destination="$DEVLOOP_REPORT_DIR/node.xml")
[ $node = 1 ] && { node --test ${report[@]+"${report[@]}"} tests/unit/node/*.test.mjs >"$out/node" 2>&1; echo $? >"$out/node.exit"; } &
[ $py = 1 ] && { python3 tests/unit/python/test_dev_loop.py >"$out/py" 2>&1; echo $? >"$out/py.exit"; } &
wait

status=0
for suite in node py; do
  [ -f "$out/$suite.exit" ] || continue
  cat "$out/$suite"
  [ "$(cat "$out/$suite.exit")" = 0 ] || status=1
done
if [ -n "${DEVLOOP_REPORT_DIR:-}" ]; then
  python3 - "$DEVLOOP_REPORT_DIR" <<'EOF'
import sys, xml.etree.ElementTree as ET
from pathlib import Path
for xml in sorted(Path(sys.argv[1]).glob("*.xml")):
    for case in ET.parse(xml).getroot().iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            print(f"FAILED {case.get('classname')}::{case.get('name')}")
EOF
fi
exit $status
