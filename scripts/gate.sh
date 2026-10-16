#!/bin/bash
# dev-loop gate for this repo: the installer suite (node) and the skill suite (python), side by side.
# GATE_SCOPE=changed (the Stop hook): only the suites the uncommitted changes reach. Else both.
# DEVLOOP_REPORT_DIR (devloop.py gate): JUnit XML of each suite there, and one FAILED line per failed test.
set -u
cd "$(dirname "$0")/.."

node=1 py=1
if [ "${GATE_SCOPE:-}" = changed ]; then
  node=0 py=0
  while IFS= read -r path; do
    case "$path" in
      skill/*|claude/*|codex/*|bin/*|lib/*|AGENTS.md|package.json) py=1 ;;
    esac
    case "$path" in
      skill/scripts/*.py|skill/scripts/*/*.py) ;;
      *) node=1 ;;
    esac
  done < <(git diff --name-only HEAD; git ls-files --others --exclude-standard)
fi

out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT
report=()
[ -n "${DEVLOOP_REPORT_DIR:-}" ] && report=(--test-reporter=spec --test-reporter-destination=stdout
  --test-reporter=junit --test-reporter-destination="$DEVLOOP_REPORT_DIR/node.xml")
[ $node = 1 ] && { node --test ${report[@]+"${report[@]}"} test/*.test.mjs >"$out/node" 2>&1; echo $? >"$out/node.exit"; } &
[ $py = 1 ] && { python3 skill/scripts/test_dev_loop.py >"$out/py" 2>&1; echo $? >"$out/py.exit"; } &
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
