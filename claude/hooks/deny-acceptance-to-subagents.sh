#!/bin/bash
# Net for subagents that do not use the implementer definition.
# agent_id is present only inside a subagent call.
# spec-tester is exempt: it authors tests/acceptance. Its own frontmatter hook blocks src/.
#
# WHAT THIS IS. A workflow guard against a cooperative agent wandering into the
# held-out suite, not a security boundary. A subagent that wants past it can
# always split a shell command (`cd tests && cat acceptance/x`) or wildcard the
# suite's own name out of the path (`cat tests/*/x`, or a Grep glob of
# `tests/*/**`). No substring rule closes those; only a resolve-and-compare
# check would, and the Codex permission profiles are the real filesystem wall.
# Treat a block as a signal to stop and report, never as something to work
# around: an agent that disguises its input has defeated the only protection
# the held-out suite has.
#
# Match path-bearing fields only, never the whole tool input: a subagent that
# writes a doc or a config whose BODY names the suite is not reading it, and
# blocking that produces a false positive that teaches evasion.
# Every field a tool reaches a path through is listed, and path is joined to
# both glob and pattern, because Grep and Glob each split a location that way.
# Each candidate token is also resolved against the subagent's working
# directory, and the directory itself is a candidate: a shell that sits in
# tests/ or inside the suite cannot reach it through a relative path.
# Candidates are normalized before matching, so a trailing slash, a doubled
# slash, a dot segment or a capital letter does not walk past the wall.
IN=$(cat)
[ -z "$(echo "$IN" | jq -r '.agent_id // empty')" ] && exit 0
[ "$(echo "$IN" | jq -r '.agent_type // empty')" = "spec-tester" ] && exit 0

CANDIDATES=$(echo "$IN" | jq -r '
  def fields:
    [ .tool_input.file_path?, .tool_input.path?, .tool_input.notebook_path?,
      .tool_input.pattern?, .tool_input.command?, .tool_input.glob?,
      .tool_input.paths?, .tool_input.files?, .tool_input.url?,
      .tool_input.source?, .tool_input.destination?, .tool_input.directory? ]
    | [ .[] | .. | strings ];
  def joins:
    [ (.tool_input.path? | .. | strings) as $p
      | ((.tool_input.glob?, .tool_input.pattern?) | .. | strings) as $g
      | $p + "/" + $g ];
  def relative($all):
    (.cwd // "") as $c
    | if $c == "" then [] else
        [$c] + [ $all[] | splits("[\\s;|&()<>\\x27\"`=]+") | select(length > 0) | $c + "/" + . ]
      end;
  (fields + joins) as $all | ($all + relative($all)) | join("\n")' \
  | tr 'A-Z' 'a-z' \
  | sed -e 's|//*|/|g' -e 's|/\./|/|g' -e ':a' -e 's|[^/][^/]*/\.\./||g' -e 'ta')

if echo "$CANDIDATES" | grep -q 'tests/acceptance'; then
  echo "Blocked: held-out suite. Subagents cannot read it." >&2
  exit 2
fi
exit 0
