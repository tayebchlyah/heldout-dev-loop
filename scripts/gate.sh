#!/bin/bash
# dev-loop gate for this repo: the installer suite (node) and the skill suite (python), side by side.
# GATE_SCOPE=changed (the Stop hook), #327: scripts/impact_map.py select picks the suites and test modules
# from the impact map tests/impact-map.json (#327); a select failure runs both suites (#327).
# Without scripts/impact_map.py (#327): any change reaches the node suite, the skill suite only for a path
# of the case list below (#327). No change: neither. Else both. A run of either suite waits for the suite
# lock (scripts/suite_lock.sh, when beside this file): one suite run at a time across the worktrees.
# DEVLOOP_REPORT_DIR (devloop.py gate): JUnit XML of each suite there.
# Both suites run with an empty TMPDIR: anything the rule of scripts/temp_leak.sh does not allow fails the gate
# (a temp leak).
set -u
cd "$(dirname "$0")/.."
. scripts/temp_leak.sh

# core.fsmonitor in a user or repo config starts a daemon in each throwaway repo a test makes,
# and the daemon outlives the test. This override wins over every config file. It comes before
# the first git call and appends to the caller's own GIT_CONFIG_* entries.
GIT_CONFIG_N=${GIT_CONFIG_COUNT:-0}
export "GIT_CONFIG_KEY_$GIT_CONFIG_N=core.fsmonitor" "GIT_CONFIG_VALUE_$GIT_CONFIG_N=false" GIT_CONFIG_COUNT=$((GIT_CONFIG_N + 1))

unset DEVLOOP_TEST_MODULES
node=1 py=1 py_modules="" node_targets=(tests/unit/node/*.test.mjs)
if [ "${GATE_SCOPE:-}" = changed ]; then
  node=0 py=0
  changed=$(git -c core.quotePath=false diff --name-only --no-renames HEAD; git -c core.quotePath=false ls-files --others --exclude-standard)
  if [ ! -f scripts/impact_map.py ]; then
    while IFS= read -r path; do
      case "$path" in
        skill/*|skills/*|claude/*|codex/*|bin/*|lib/*|tests/unit/python/*|reference/*|AGENTS.md|README.md|CODING_STANDARDS.md|package.json|upstream.json|scripts/upstream_watch.py|scripts/acceptance.sh|scripts/repo_rules.py|scripts/gate.sh|scripts/temp_leak.sh|scripts/suite_lock.sh|scripts/mutation_canary.sh|tests/fixtures/*|cliff.toml) py=1 ;;
      esac
      node=1
    done < <(printf '%s\n' "$changed" | sed '/^$/d')
  elif picked=$(printf '%s\n' "$changed" | python3 scripts/impact_map.py select) \
    && py_line=$(printf '%s\n' "$picked" | sed -n 1p) && node_line=$(printf '%s\n' "$picked" | sed -n 2p) \
    && case "$py_line" in py|"py "*) true ;; *) false ;; esac \
    && case "$node_line" in node|"node "*) true ;; *) false ;; esac; then
    py_words=${py_line#py} node_words=${node_line#node}
    py_words=${py_words# } node_words=${node_words# }
    case "$py_words" in "") ;; all) py=1 ;; *) py=1 py_modules=$py_words ;; esac
    case "$node_words" in "") ;; all) node=1 ;; *) node=1 node_targets=($node_words) ;; esac
  else
    node=1 py=1
  fi
fi

# The node suite drives a real Chromium (drive.test.mjs). The gate installs nothing itself.
if [ $node = 1 ] && [ ! -d node_modules/playwright ]; then
  echo "FAILED setup: run npm ci && npx playwright install chromium"
  exit 1
fi
if [ $node$py != 00 ] && [ -f scripts/suite_lock.sh ]; then
  . scripts/suite_lock.sh
  suite_lock || exit 1
fi

out=$(mktemp -d) scratch=$(mktemp -d)
trap 'rm -rf "$out" "$scratch"' EXIT
report=()
[ -n "${DEVLOOP_REPORT_DIR:-}" ] && report=(--test-reporter=spec --test-reporter-destination=stdout
  --test-reporter=junit --test-reporter-destination="$DEVLOOP_REPORT_DIR/node.xml")
export TMPDIR="$scratch"
[ $node = 1 ] && { node --test ${report[@]+"${report[@]}"} "${node_targets[@]}" >"$out/node" 2>&1; echo $? >"$out/node.exit"; } 9>&- &
[ $py = 1 ] && { env ${py_modules:+"DEVLOOP_TEST_MODULES=$py_modules"} python3 tests/unit/python/test_dev_loop.py >"$out/py" 2>&1; echo $? >"$out/py.exit"; } 9>&- &
wait

status=0
for suite in node py; do
  [ -f "$out/$suite.exit" ] || continue
  cat "$out/$suite"
  [ "$(cat "$out/$suite.exit")" = 0 ] || status=1
done
left=$(temp_leak "$scratch")
if [ -n "$left" ]; then
  printf 'temp leak, the tests left in TMPDIR:\n%s\n' "$left"
  status=1
fi
if [ -n "${DEVLOOP_REPORT_DIR:-}" ]; then
  python3 - "$DEVLOOP_REPORT_DIR" "$left" <<'EOF' || status=1
import json, re, sys, tempfile
from pathlib import Path
sys.path.insert(0, "tests/unit/python")
from junit_report import write_junit
reports, left = Path(sys.argv[1]), sys.argv[2]
left = re.sub("[^\t\n\r\x20-퟿-�\U00010000-\U0010ffff]", lambda m: f"\\x{ord(m[0]):02x}", left)
with tempfile.TemporaryDirectory() as outcomes:
    case = {"seconds": 0.0, "outcome": "failure" if left else "pass", "text": left}
    Path(outcomes, "temp-leak.json").write_text(json.dumps({"temp-leak.TMPDIR": case}))
    write_junit(reports / "temp-leak.xml", Path(outcomes), "temp-leak")
EOF
fi
exit $status
