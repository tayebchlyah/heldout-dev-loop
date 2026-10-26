#!/bin/bash
# Held-out acceptance suite of this repo (commands.acceptance, CI job `acceptance`): the Python tests and the
# Node tests of <folder> (default tests/acceptance), both always run. DEVLOOP_REPORT_DIR set: JUnit XML there,
# acceptance-python.xml and acceptance-node.xml. Exit 1 when one part fails.
set -u
cd "$(dirname "$0")/.."
folder=${1:-tests/acceptance}

py_report=() node_report=()
if [ -n "${DEVLOOP_REPORT_DIR:-}" ]; then
  py_report=("$DEVLOOP_REPORT_DIR/acceptance-python.xml")
  node_report=(--test-reporter=spec --test-reporter-destination=stdout
    --test-reporter=junit --test-reporter-destination="$DEVLOOP_REPORT_DIR/acceptance-node.xml")
fi

status=0
python3 tests/unit/python/test_dev_loop.py --discover "$folder" ${py_report[@]+"${py_report[@]}"} || status=1
node --test ${node_report[@]+"${node_report[@]}"} "$folder"/*.test.mjs || status=1
exit $status
