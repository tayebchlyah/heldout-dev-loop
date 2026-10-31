#!/bin/bash
# Held-out acceptance suite of this repo (commands.acceptance, CI job `acceptance`): the Python tests and the
# Node tests of <folder> (default tests/acceptance), both always run, in an empty TMPDIR: a leftover fails the
# run and is listed (rule in scripts/temp_leak.sh). DEVLOOP_REPORT_DIR set: JUnit XML there, acceptance-python.xml,
# acceptance-node.xml and acceptance-temp-leak.xml (one case temp-leak TMPDIR, failing on a leak).
# Exit 1 when one part fails, the tests leave a temp leak, or (reports on) no held-out test ran.
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
  python3 - "$DEVLOOP_REPORT_DIR" "$left" <<'EOF' || status=1
import json, re, sys, tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
sys.path.insert(0, "tests/unit/python")
from junit_report import write_junit
reports, left = Path(sys.argv[1]), sys.argv[2]
left = re.sub("[^\t\n\r\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]", lambda m: f"\\x{ord(m[0]):02x}", left)
with tempfile.TemporaryDirectory() as outcomes:
    case = {"seconds": 0.0, "outcome": "failure" if left else "pass", "text": left}
    Path(outcomes, "temp-leak.json").write_text(json.dumps({"temp-leak.TMPDIR": case}))
    write_junit(reports / "acceptance-temp-leak.xml", Path(outcomes), "temp-leak")
ran = 0
for name in ("acceptance-python.xml", "acceptance-node.xml"):
    try:
        ran += sum(1 for c in ET.parse(reports / name).getroot().iter("testcase")
                   if c.find("skipped") is None and Path(c.get("name")).name != Path(c.get("file", "")).name)
    except (OSError, ET.ParseError):
        pass
if not ran:
    print("no held-out test ran")
    sys.exit(1)
EOF
fi
exit $status
