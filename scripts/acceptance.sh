#!/bin/bash
# Held-out acceptance suite of this repo (commands.acceptance, CI job `acceptance`): the Python tests and the
# Node tests of <folder> (default tests/acceptance), both always run, in an empty TMPDIR: a leftover fails the
# run and is listed (rule in scripts/temp_leak.sh). DEVLOOP_REPORT_DIR set: JUnit XML there, acceptance-python.xml,
# acceptance-node.xml and acceptance-temp-leak.xml (one case temp-leak TMPDIR, failing on a leak).
# Exit 1 when one part fails or the tests leave a temp leak.
set -u
cd "$(dirname "$0")/.."
. scripts/temp_leak.sh
folder=${1:-tests/acceptance}
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT

py_report=() node_report=()
if [ -n "${DEVLOOP_REPORT_DIR:-}" ]; then
  py_report=("$DEVLOOP_REPORT_DIR/acceptance-python.xml")
  node_report=(--test-reporter=spec --test-reporter-destination=stdout
    --test-reporter=junit --test-reporter-destination="$DEVLOOP_REPORT_DIR/acceptance-node.xml")
fi

status=0
TMPDIR="$scratch" python3 tests/unit/python/test_dev_loop.py --discover "$folder" ${py_report[@]+"${py_report[@]}"} || status=1
TMPDIR="$scratch" node --test ${node_report[@]+"${node_report[@]}"} "$folder"/*.test.mjs || status=1
left=$(temp_leak "$scratch")
if [ -n "$left" ]; then
  printf 'temp leak, the tests left in TMPDIR:\n%s\n' "$left"
  status=1
fi
if [ -n "${DEVLOOP_REPORT_DIR:-}" ]; then
  python3 - "$DEVLOOP_REPORT_DIR/acceptance-temp-leak.xml" "$left" <<'EOF'
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, "tests/unit/python")
from junit_report import write_junit
with tempfile.TemporaryDirectory() as outcomes:
    case = {"seconds": 0.0, "outcome": "failure" if sys.argv[2] else "pass", "text": sys.argv[2]}
    Path(outcomes, "temp-leak.json").write_text(json.dumps({"temp-leak.TMPDIR": case}))
    write_junit(Path(sys.argv[1]), Path(outcomes), "temp-leak")
EOF
fi
exit $status
