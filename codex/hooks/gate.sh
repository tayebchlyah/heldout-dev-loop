#!/bin/sh
# Stop and SubagentStop gate. Run a dirty repository's commands.gate from
# dev-loop.json (sh -c) when mapped, else its executable scripts/gate.sh.
# The hook asks for the selective gate (GATE_SCOPE=changed). A full gate is a direct call.
. "${0%/*}/git-pin.sh"
input=$(cat)
# Refresh the progress board in the background. Called on every path that does not block.
board() {
  ev=stop
  [ "$(printf '%s' "$input" | jq -r '.hook_event_name // empty' 2>/dev/null)" = SubagentStop ] && ev=subagent-stop
  printf '%s' "$input" | sh "$(dirname "$0")/board.sh" "$ev"
}
[ "$(printf '%s' "$input" | jq -r '.stop_hook_active // false' 2>/dev/null)" = true ] && { board; exit 0; }
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)
[ -n "$cwd" ] || { board; exit 0; }
cd "$cwd" 2>/dev/null || { board; exit 0; }
gate=$(python3 -I -c 'import json; c = json.load(open("dev-loop.json")).get("commands", {}).get("gate"); print(c if isinstance(c, str) else "")' 2>/dev/null)
[ -n "$gate" ] || [ -x scripts/gate.sh ] || { board; exit 0; }
git_pin_repo .
dirty=$(git status --porcelain --ignore-submodules=dirty 2>/dev/null) && [ -z "$dirty" ] && { board; exit 0; }

if [ -n "$gate" ]; then set -- sh -c "$gate"; else set -- scripts/gate.sh; fi
main=$KIT_MAIN
# The gate writes to an unnamed temp file this hook holds open (#200): no path for the confined gate to swap, and a
# child it leaves running neither holds the hook nor adds to the status, the launcher's own exit.
reason=$(GATE_SCOPE=changed python3 -I -c 'import subprocess, sys, tempfile
with tempfile.TemporaryFile() as out:
    code = subprocess.run(sys.argv[1:], stdin=subprocess.DEVNULL, stdout=out, stderr=out).returncode
    out.seek(max(0, out.tell() - 3000))
    sys.stdout.buffer.write(out.read())
sys.exit(code)' python3 -I "$(dirname "$0")/sandbox.py" "$main" "$cwd" -- "$@")
status=$?
if [ "$status" -eq 0 ]; then
  board
else
  lead="Gate failed. Fix it. Do not weaken or skip tests."
  if [ "$status" -eq 125 ] && printf '%s' "$reason" | grep -q 'Sandbox startup failed'; then
    lead="The sandbox could not start. This is an environment problem, not a test failure."
  fi
  jq -n --arg lead "$lead" --arg reason "$reason" '{decision:"block",reason:($lead + "\n\n" + $reason)}'
fi
exit 0
