#!/bin/bash
# Net for subagents that do not use the implementer definition.
# agent_id is present only inside a subagent call.
# spec-tester is exempt: it authors tests/acceptance. Its own frontmatter hook blocks src/.
# Match path-bearing fields only, never the whole tool input: a subagent that
# writes a doc or a config whose BODY names the suite is not reading it, and
# blocking that teaches agents to work around the wall instead of reporting it.
# Every field a tool can reach a path through must be listed here, glob
# included, and path+glob is also joined: Grep splits a location across the two.
IN=$(cat)
[ -z "$(echo "$IN" | jq -r '.agent_id // empty')" ] && exit 0
[ "$(echo "$IN" | jq -r '.agent_type // empty')" = "spec-tester" ] && exit 0
PATHS=$(echo "$IN" | jq -r '
  [.tool_input.file_path?, .tool_input.path?, .tool_input.notebook_path?,
   .tool_input.pattern?, .tool_input.command?, .tool_input.glob?,
   .tool_input.paths?, .tool_input.files?]
  | flatten | map(select(type == "string")) | join("\n")')
JOINED=$(echo "$IN" | jq -r '
  if (.tool_input.path? | type) == "string" and (.tool_input.glob? | type) == "string"
  then .tool_input.path + "/" + .tool_input.glob else empty end')
if printf '%s\n%s' "$PATHS" "$JOINED" | grep -q 'tests/acceptance'; then
  echo "Blocked: held-out suite. Subagents cannot read it." >&2
  exit 2
fi
exit 0
