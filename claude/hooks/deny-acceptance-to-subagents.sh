#!/bin/bash
# Wall around tests/acceptance for every caller but spec-tester (D2).
# agent_id is present only inside a subagent call.
# Main session: no Edit, Write or NotebookEdit under tests/acceptance/. Its reads stay
# open: Stage 6 reads a failing test to classify it. A Bash write is left to seal-check.
# Subagents: the net below, for those that do not use the implementer definition.
# spec-tester is exempt: it authors tests/acceptance. It gets the source wall instead: no path
# names src/, app/, backend/ or frontend/ (the segment match of deny-path.sh, in this folder).
# Its frontmatter hook holds the same rule, but did not fire in Claude Code 2.1.309 (canary).
#
# WHAT THIS IS. A workflow guard against a cooperative agent wandering into the
# held-out suite, not a security boundary. A subagent that wants past it can
# always hide a folder in a variable (`d=tests; cd $d && cat acceptance/x`) or
# wildcard the suite's own name out of the path (`cat tests/*/x`, or a Grep glob
# of `tests/*/**`). No substring rule closes those; only a resolve-and-compare
# check would. The walls are hooks; the transcript audit backs them up.
# Treat a block as a signal to stop and report, never as something to work
# around: an agent that disguises its input has defeated the hook wall. One exception: after a
# search-scope block, search again on named source folders or with the suite excluded.
#
# Match path-bearing fields only, never the whole tool input: a subagent that
# writes a doc or a config whose BODY names the suite is not reading it, and
# blocking that produces a false positive that teaches evasion.
# Every field a tool reaches a path through is listed, and path is joined to
# both glob and pattern, because Grep and Glob each split a location that way.
# Each candidate token is also resolved against the folder a cd or pushd
# earlier in the command has moved to (else the subagent's working directory),
# and the cwd and those folders are candidates: a shell that sits in tests/ or inside the suite,
# or a `cd tests && cat acceptance/x`, cannot reach it through a relative path.
# Edit, Write and NotebookEdit of an installed kit file are denied to every
# caller: kit files change only by upgrade.
# Candidates are normalized before matching, so a trailing slash, a doubled
# slash, a dot segment or a capital letter does not walk past the wall.
# Search rule (#91): a subagent but spec-tester cannot run a recursive search (Grep, Glob, grep -r,
# rg, git grep, find) whose root is tests/acceptance or a folder above it, unless it excludes the
# suite; the engine is devloop.py search-wall of the kit, skipped when no kit CLI is found, and a
# CLI error blocks. Not walled: wrappers (xargs, env, command, sh -c, bash -c, eval), and ls -R, tree,
# fd, ag, ack, git ls-files. The audit path rule backs up the search tools.
# Tool walls (#97): LSP is denied to every subagent, spec-tester included: its results name files the
# call does not. MCP tools (mcp__*, ListMcpResourcesTool, ReadMcpResourceTool, ReadMcpResourceDirTool)
# are denied to implementer and spec-tester; any other subagent gets the name rule on the path fields,
# filePath and uri included, a file:// value by its path. Monitor is walled as Bash on its command:
# the name rule, the search rule and the audit. Open for other subagents: an MCP search tool, an MCP
# tool that runs code or reads a remote copy of the repo, a resource listing; the audit reads their
# results. A project agent's frontmatter hooks are skipped in an untrusted folder (#139).
GIT_CONFIG_N=${GIT_CONFIG_COUNT:-0}
export "GIT_CONFIG_KEY_$GIT_CONFIG_N=core.fsmonitor" "GIT_CONFIG_VALUE_$GIT_CONFIG_N=false" GIT_CONFIG_COUNT=$((GIT_CONFIG_N + 1))
IN=$(cat)
# Fail closed (D8): no jq, or input that is not a JSON object, blocks the call.
fail() { echo "Blocked: guard error: $1" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || fail "jq is missing"
[ -n "$IN" ] || fail "empty hook input"
printf '%s' "$IN" | jq -e 'type == "object"' >/dev/null 2>&1 || fail "hook input is not a JSON object"
normalize() { tr 'A-Z' 'a-z' | sed -e 's|//*|/|g' -e 's|/\./|/|g' -e ':a' -e 's|[^/][^/]*/\.\./||g' -e 'ta'; }
WORD='(^|[^[:alnum:]_.-])([efr]?grep|rg|find)([^[:alnum:]_.-]|$)'
search_wall() {
  local dir top cli= out
  for dir in "$(printf '%s' "$1" | jq -r '.cwd // empty')" "$CLAUDE_PROJECT_DIR"; do
    [ -n "$dir" ] || continue
    top=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null) || continue
    [ -f "$top/.agents/skills/dev-loop/scripts/devloop.py" ] && { cli=$top/.agents/skills/dev-loop/scripts/devloop.py; break; }
  done
  [ -n "$cli" ] || return 0
  out=$(printf '%s' "$1" | python3 "$cli" search-wall 2>/dev/null) || fail "search check failed"
  printf '%s' "$out" | jq -e 'type == "object"' >/dev/null 2>&1 || fail "search check failed"
  [ "$(printf '%s' "$out" | jq -r '.deny')" = true ] || return 0
  printf '%s\n' "$(printf '%s' "$out" | jq -r '.reason')" >&2
  exit 2
}

# Installed kit files: the folders, the rules file and the agents init writes (kit list in lib/kit.mjs),
# matched on repo-relative paths only: a user-level ~/.claude/agents/implementer.md is not a kit file.
KIT='^(\.agents/skills/(dev-loop|pause|continue|release-audit|garden|release|release-notes)|\.claude/skills/(dev-loop|pause|continue|release-audit|garden|release|release-notes)|\.dev-loop)(/|$)|^\.codex/rules/dev-loop\.rules$|^\.claude/agents/(implementer|spec-tester)\.md$|^\.codex/agents/(final-reviewer|implementer|planner|security-reviewer|spec-tester|task-reviewer)\.toml$'
# Normalized paths on stdin, repo top in $1: a path under the top (or its /private variant) becomes
# repo-relative, any other absolute path is dropped (not a kit path), a leading ./ goes.
repo_rel() { awk -v t="$1" 'BEGIN { p = index(t, "/private/") == 1 ? substr(t, 9) : "/private" t }
    /^\// { if (t != "" && index($0, t "/") == 1) $0 = substr($0, length(t) + 2)
            else if (t != "" && index($0, p "/") == 1) $0 = substr($0, length(p) + 2)
            else next }
    { sub(/^(\.\/)+/, ""); print }'; }

TOOL=$(echo "$IN" | jq -r '.tool_name // empty')
case "$TOOL" in
  Edit|Write|NotebookEdit)
    # The path as given, and joined to cwd when relative.
    TARGETS=$(echo "$IN" | jq -r '(.cwd // "") as $c
      | (.tool_input.file_path // .tool_input.notebook_path // "") | select(. != "")
      | ., (select(startswith("/") | not) | select($c != "") | $c + "/" + .)' 2>/dev/null) || fail "path extraction failed"
    # Every caller: a kit file changes only by upgrade. A shell write is left to seal-check (kit-changed).
    # Repo top: the git top of the cwd, else the cwd itself. A relative path with no cwd stays repo-relative.
    C=$(echo "$IN" | jq -r '.cwd // empty')
    T=
    [ -z "$C" ] || T=$(git -C "$C" rev-parse --show-toplevel 2>/dev/null) || T=$C
    T=$(printf '%s' "${T%/}" | normalize)
    if echo "$IN" | jq -r '(.cwd // "") as $c | (.tool_input.file_path // .tool_input.notebook_path // "")
      | select(. != "") | if startswith("/") or $c == "" then . else $c + "/" + . end' 2>/dev/null \
      | normalize | repo_rel "$T" | grep -Eq "$KIT"; then
      echo "Blocked: installed dev-loop kit file. Kit files change only by upgrade: change dev-loop, then run upgrade." >&2
      exit 2
    fi ;;
esac

if [ -z "$(echo "$IN" | jq -r '.agent_id // empty')" ]; then
  case "$TOOL" in Edit|Write|NotebookEdit) ;; *) exit 0 ;; esac
  if printf '%s\n' "$TARGETS" | normalize | grep -Eq '(^|/)tests/acceptance(/|$)'; then
    echo "Blocked: held-out suite. Only spec-tester writes tests/acceptance. The main session may read it." >&2
    exit 2
  fi
  exit 0
fi
case "$TOOL" in
  LSP) echo "Blocked: LSP is closed to subagents: its results name files the call does not. Search named folders with Grep or Bash." >&2; exit 2 ;;
  mcp__*|ListMcpResourcesTool|ReadMcpResourceTool|ReadMcpResourceDirTool)
    case "$(echo "$IN" | jq -r '.agent_type // empty')" in
      implementer|spec-tester) echo "Blocked: MCP tools are closed to this agent. Read and search named folders with the file tools or the shell." >&2; exit 2 ;;
    esac ;;
esac
TESTER=
if [ "$(echo "$IN" | jq -r '.agent_type // empty')" = "spec-tester" ]; then
  TESTER=1
  # Relative paths resolve against the cwd as a repo path, so a repo that sits under a folder named
  # src or app is not walled whole. Outside a git repo: the cwd itself. Never empty: the command is
  # split into tokens only with a cwd.
  CWD=$(echo "$IN" | jq -r '.cwd // empty')
  # An absolute path inside the repo is matched as its repo-relative path, for the same reason.
  BASE= TOP=
  [ -z "$CWD" ] || BASE=$(git -C "$CWD" rev-parse --show-prefix 2>/dev/null) || BASE=$CWD
  [ -z "$CWD" ] || TOP=$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null) || TOP=
  IN=$(echo "$IN" | jq --arg b "${BASE:-.}" --arg t "$TOP" '
  ($t | if . == "" then [] else [., (if startswith("/private/") then .[8:] else "/private" + . end)] end
    | sort_by(-length)) as $tops
  # Mirrors the rel prelude of codex/hooks/acceptance-wall.sh (separate harness payloads).
  # "<top>/" becomes "./", never "": a path glued to a flag (-I<top>/src) keeps its segment.
  # The bare repo top becomes ".".
  | def rel: reduce $tops[] as $x (.; split($x + "/") | join("./") | split($x) | join("."));
  .cwd = $b | .tool_input |= walk(if type == "string" then rel else . end)' 2>/dev/null) || fail "cannot rewrite paths"
fi

CANDIDATES=$(echo "$IN" | jq -r '
  # A heredoc body and an exclusion (:!x, :(exclude)x, --exclude-dir=x, grep -v x)
  # name a path without reaching it.
  def find_clean: gsub("(?<o>(^|[ \t])-(path|ipath|wholename|name|iname) +)[^ \t\n;|&]+(?= +-prune)"; "\(.o)")
    | gsub("(?<o>(^|[ \t])(-not|!) +-path +)[^ \t\n;|&]+"; "\(.o)");
  def clean: gsub("<<-?[ \t]*(?<q>[\u0027\"]?)(?<d>[A-Za-z_][A-Za-z0-9_.-]*)\\k<q>(?<r>[^\n]*)\n(?:[^\n]*\n)*?\t*\\k<d>(?=\n|$)"; "<<\(.d)\(.r)")
    | gsub("(:!|:\\^|:\\([^)]*exclude[^)]*\\)|--exclude-dir=)[^ \t\n;|&]*"; "")
    | gsub("(?<g>grep( +-[A-Za-z]+)* +-[A-Za-z]*v[A-Za-z]*) +[^ \t\n;|&]+"; "\(.g)")
    | gsub("(?<o>(^|[ \t])(-g|--glob|--iglob)(=| *)[\u0027\"]?)![^ \t\n;|&\u0027\"]*"; "\(.o)")
    | gsub("(?<s>(^|[;&|\n(])[ \t]*find([ \t][^;&|\n]*)?)"; "\(.s | find_clean)");
  def unless_excluded: if type == "string" and startswith("!") then empty else . end;
  def fields:
    [ .tool_input.file_path?, .tool_input.path?, .tool_input.notebook_path?,
      .tool_input.pattern?, (.tool_input.command? | strings | clean), (.tool_input.glob? | unless_excluded),
      .tool_input.paths?, .tool_input.files?, .tool_input.url?,
      .tool_input.source?, .tool_input.destination?, .tool_input.directory?,
      .tool_input.filePath?, .tool_input.uri? ]
    | [ .[] | .. | strings | ., (select(startswith("file://")) | ltrimstr("file://") | sub("^localhost(?=/)"; "")) ];
  def joins:
    [ (.tool_input.path? | .. | strings) as $p
      | (((.tool_input.glob? | unless_excluded), .tool_input.pattern?) | .. | strings) as $g
      | $p + "/" + $g ];
  # A token walk over each field, in order: the folder starts at the cwd; a cd or pushd token makes the next
  # token not starting with - (options, --) its target, absolute or joined to the folder, and a folder
  # reached; any other token is joined to the folder at that point.
  # Subshell scope is not tracked: after (cd x && ...), a later token still joins to x.
  def relative($all):
    if (.cwd // "") == "" then [] else
      .cwd as $c
      | [$c] + [ $all[] | reduce (splits("[\\s;|&()<>\\x27\"`=]+") | select(length > 0)) as $t
          ({w: $c, cd: false, out: []};
            if .cd and ($t | startswith("-")) then .
            elif .cd then .w = (if ($t | startswith("/")) then $t else .w + "/" + $t end) | .cd = false | .out += [.w]
            elif $t == "cd" or $t == "pushd" then .cd = true
            else .out += [.w + "/" + $t] end)
        | .out[] ]
    end;
  (fields + joins) as $all | ($all + relative($all)) | join("\n")' 2>/dev/null) || fail "path extraction failed"
CANDIDATES=$(printf '%s' "$CANDIDATES" | normalize)

if [ -n "$TESTER" ]; then
  CALL=$(printf '%s' "$CANDIDATES" | jq -Rn '{tool_input: {paths: [inputs]}}' 2>/dev/null) || fail "cannot pass the paths on"
  printf '%s' "$CALL" | bash "$(dirname "$0")/deny-path.sh" src/ app/ backend/ frontend/ || exit 2
  exit 0
fi

if echo "$CANDIDATES" | grep -q 'tests/acceptance'; then
  echo "Blocked: held-out suite. Subagents cannot read it." >&2
  exit 2
fi
case "$(printf '%s' "$IN" | jq -r '.tool_name // empty')" in
  Grep|Glob) search_wall "$IN" ;;
  Bash|Monitor) printf '%s' "$IN" | jq -r '.tool_input.command // empty' | grep -Eq "$WORD" && search_wall "$IN" ;;
esac
exit 0
