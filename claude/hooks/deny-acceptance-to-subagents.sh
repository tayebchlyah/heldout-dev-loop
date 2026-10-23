#!/bin/bash
# Wall around tests/acceptance for every caller but spec-tester (D2).
# agent_id is present only inside a subagent call.
# Main session: no Edit, Write or NotebookEdit under tests/acceptance/. Its reads stay
# open: Stage 6 reads a failing test to classify it. A Bash write is left to seal-check.
# Subagents: the net below, for those that do not use the implementer definition.
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
# Fail closed (D8): no jq, or input that is not a JSON object, blocks the call.
fail() { echo "Blocked: guard error: $1" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || fail "jq is missing"
[ -n "$IN" ] || fail "empty hook input"
printf '%s' "$IN" | jq -e 'type == "object"' >/dev/null 2>&1 || fail "hook input is not a JSON object"
normalize() { tr 'A-Z' 'a-z' | sed -e 's|//*|/|g' -e 's|/\./|/|g' -e ':a' -e 's|[^/][^/]*/\.\./||g' -e 'ta'; }

if [ -z "$(echo "$IN" | jq -r '.agent_id // empty')" ]; then
  case "$(echo "$IN" | jq -r '.tool_name // empty')" in Edit|Write|NotebookEdit) ;; *) exit 0 ;; esac
  # The path as given, and joined to cwd when relative.
  TARGETS=$(echo "$IN" | jq -r '(.cwd // "") as $c
    | (.tool_input.file_path // .tool_input.notebook_path // "") | select(. != "")
    | ., (select(startswith("/") | not) | select($c != "") | $c + "/" + .)') || fail "path extraction failed"
  if printf '%s\n' "$TARGETS" | normalize | grep -Eq '(^|/)tests/acceptance(/|$)'; then
    echo "Blocked: held-out suite. Only spec-tester writes tests/acceptance. The main session may read it." >&2
    exit 2
  fi
  exit 0
fi
[ "$(echo "$IN" | jq -r '.agent_type // empty')" = "spec-tester" ] && exit 0

CANDIDATES=$(echo "$IN" | jq -r '
  # A heredoc body and an exclusion (:!x, :(exclude)x, --exclude-dir=x, grep -v x)
  # name a path without reaching it.
  def clean: gsub("<<-?[ \t]*(?<q>[\u0027\"]?)(?<d>[A-Za-z_][A-Za-z0-9_.-]*)\\k<q>(?<r>[^\n]*)\n(?:[^\n]*\n)*?\t*\\k<d>(?=\n|$)"; "<<\(.d)\(.r)")
    | gsub("(:!|:\\([^)]*exclude[^)]*\\)|--exclude-dir=)[^ \t\n;|&]*"; "")
    | gsub("(?<g>grep( +-[A-Za-z]+)* +-[A-Za-z]*v[A-Za-z]*) +[^ \t\n;|&]+"; "\(.g)");
  def fields:
    [ .tool_input.file_path?, .tool_input.path?, .tool_input.notebook_path?,
      .tool_input.pattern?, (.tool_input.command? | strings | clean), .tool_input.glob?,
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
  (fields + joins) as $all | ($all + relative($all)) | join("\n")') || fail "path extraction failed"
CANDIDATES=$(printf '%s' "$CANDIDATES" | normalize)

if echo "$CANDIDATES" | grep -q 'tests/acceptance'; then
  echo "Blocked: held-out suite. Subagents cannot read it." >&2
  exit 2
fi
exit 0
