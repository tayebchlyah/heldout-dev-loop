#!/bin/bash
# Net for subagents that do not use the implementer definition.
# agent_id is present only inside a subagent call.
# spec-tester is exempt: it authors tests/acceptance. Its own frontmatter hook blocks src/.
# Match path-bearing fields only, never the whole tool input: a subagent that
# writes a doc or a config whose BODY names the suite is not reading it, and
# blocking that teaches agents to work around the wall instead of reporting it.
IN=$(cat)
[ -z "$(echo "$IN" | jq -r '.agent_id // empty')" ] && exit 0
[ "$(echo "$IN" | jq -r '.agent_type // empty')" = "spec-tester" ] && exit 0
PATHS=$(echo "$IN" | jq -r '
  [.tool_input.file_path?, .tool_input.path?, .tool_input.notebook_path?,
   .tool_input.pattern?, .tool_input.command?]
  | map(select(type == "string")) | join("\n")')
if echo "$PATHS" | grep -q 'tests/acceptance'; then
  echo "Blocked: held-out suite. Subagents cannot read it." >&2
  exit 2
fi
exit 0
