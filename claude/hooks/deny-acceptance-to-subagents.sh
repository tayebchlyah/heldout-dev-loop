#!/bin/bash
# Net for subagents that do not use the implementer definition.
# agent_id is present only inside a subagent call.
# spec-tester is exempt: it authors tests/acceptance. Its own frontmatter hook blocks src/.
IN=$(cat)
[ -z "$(echo "$IN" | jq -r '.agent_id // empty')" ] && exit 0
[ "$(echo "$IN" | jq -r '.agent_type // empty')" = "spec-tester" ] && exit 0
if echo "$IN" | jq -r '.tool_input | tostring' | grep -q 'tests/acceptance'; then
  echo "Blocked: held-out suite. Subagents cannot read it." >&2
  exit 2
fi
exit 0
