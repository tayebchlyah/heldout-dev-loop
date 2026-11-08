#!/bin/sh
# Codex PreToolUse wall (D9), matcher .* : every tool call that fires a hook, because a future tool
# name cannot be listed.
# agent_id is present only in a subagent call.
# spec_tester: no path names src/, app/, backend/ or frontend/. Any other subagent: no path names
# tests/acceptance. Main session: no apply_patch of a file under tests/acceptance/; its Bash stays open.
# apply_patch (and its Edit, Write aliases): the paths are the patch file headers, never the body.
# Every caller: no apply_patch of an installed kit file (kit files change only by upgrade).
# Deny: exit 2 and one stderr line. The path match is deny-path.sh, in this folder.
#
# A workflow guard, not a security boundary: a block means stop and report, never rephrase the call.
# One exception: after a search-scope block, search again on named source folders or with the suite excluded.
# Search rule (#91): the Bash of a subagent but spec_tester cannot run a recursive search (grep -r, rg,
# git grep, find) whose root is tests/acceptance or a folder above it, unless it excludes the
# suite; the engine is devloop.py search-wall of the kit, skipped when no kit CLI is found, and a
# CLI error blocks. Not walled: wrappers (xargs, env, command, sh -c, bash -c, eval), and ls -R, tree,
# fd, ag, ack, git ls-files. The audit path rule backs up the search tools.
# Tool walls (#97): MCP tools (mcp__*, read_mcp_resource, list_mcp_resources, list_mcp_resource_templates)
# are denied to implementer and spec_tester; any other subagent gets the name rule on the path fields,
# filePath and uri included, a file:// value by its path. view_image is walled by path: spec_tester the
# source folders, any other subagent tests/acceptance. The main session keeps every tool. Code mode: no
# hook fires on exec, one fires on each inner tool call. No hook fires on write_stdin: the kit config
# sets features.unified_exec_tty = false, so no tty shell stays open to type into (#140). Not walled:
# for other subagents an MCP search tool, an MCP tool that runs code or reads a remote copy, a resource
# listing; the audit reads their results.
# Web tools (#240): a tool whose name starts with web (web.*) is denied to implementer and spec_tester with
# agent_id set, whatever its input; every other caller exits 0 with no path rule. The hosted web
# search tool reaches no hook. The denial names the NEEDS_FACT exit.
# Role tool list (#283): implementer and spec_tester with agent_id set may call only Bash, apply_patch,
# Edit, Write, view_image, update_plan, clockcurr_time, clocksleep, get_context_remaining, skillslist,
# skillsread and skillsinstallations, after the web and MCP rules above. Edit and Write are matcher
# aliases of apply_patch and take its patch rule; skillsread gets the path rule on every input string,
# at any depth. Every other name, a missing one included, is denied to them, whatever its input: the
# codex_tui task tools (codex_tuicreate_thread, codex_tuifork_thread, ...), the collaboration and
# multi_agent tools, a future tool. The denial names the NEEDS_FACT exit. Every other caller exits 0 on
# an unlisted name, with no path rule. Written gaps: no hook fires on write_stdin, the outer exec and
# wait of code mode, tool_search, the hosted web search tool or a freeform extension tool; a subagent
# other than these 2 keeps the TUI task tools, and a TUI thread has no agent_id, so it gets the main
# session rules.
# Collaboration tools (#273): Codex 0.166.0 gives the v1 spawn bare, every other v1 tool as
# multi_agent_v1<name>; no role setting closes these tools; collaboration.*, the multi_agent names and
# the bare v1 names fall under the role tool list, which denies them. An mcp__ tool
# keeps the MCP rule, whatever its name ends with. No tools key or skill list in the Codex agent files:
# Codex has no Skill tool that starts a fork. Written gap: a subagent other than these 2 keeps the
# tools, and can still start a spec_tester child.
# No wall on a nested harness started from the shell (claude -p, codex exec, another agent CLI): the
# child has no agent_id, so it gets the main session rules, reads open. The audit does not read its
# transcript, only what it prints to the parent. A deny by name does not close the route.
. "${0%/*}/git-pin.sh"
input=$(cat)
# Fail closed (D8): no jq, or input that is not a JSON object, blocks the call.
fail() { echo "Blocked: guard error: $1" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || fail "jq is missing"
[ -n "$input" ] || fail "empty hook input"
printf '%s' "$input" | jq -e 'type == "object"' >/dev/null 2>&1 || fail "hook input is not a JSON object"
field() { printf '%s' "$input" | jq -r "$1 // empty"; }
agent_id=$(field .agent_id) agent_type=$(field .agent_type) tool=$(field .tool_name) cwd=$(field .cwd)

case "$tool" in
  web*)
    if [ -n "$agent_id" ]; then
      case "$agent_type" in
        implementer|spec_tester)
          echo "Blocked: $tool is closed to this agent: the web holds a public copy of the repository. For an outside fact the brief and the repository do not give, stop and report NEEDS_FACT: <question>." >&2
          exit 2 ;;
      esac
    fi
    exit 0 ;;
  mcp__*|read_mcp_resource|list_mcp_resources|list_mcp_resource_templates) ;;
  *)
    if [ -n "$agent_id" ]; then
      case "$agent_type" in
        implementer|spec_tester)
          case "$tool" in
            Bash|apply_patch|Edit|Write|view_image|update_plan|clockcurr_time|clocksleep|get_context_remaining|skillslist|skillsread|skillsinstallations) ;;
            *)
              echo "Blocked: ${tool:-(no tool name)} is closed to this agent: it is not on the tool list of this role. For an outside fact the brief and the repository do not give, stop and report NEEDS_FACT: <question>." >&2
              exit 2 ;;
          esac ;;
      esac
    fi ;;
esac

if [ -n "$agent_id" ] && [ "$agent_type" = spec_tester ]; then
  set -- src/ app/ backend/ frontend/
else
  set -- tests/acceptance
fi
# Relative paths resolve against the cwd as a repo path, so a repo that sits under a folder named src
# or app is not walled whole. Outside a git repo: the cwd itself. Never empty: deny-path.sh splits
# the command into tokens only with a cwd. An absolute path inside the repo is matched as its
# repo-relative path, for the same reason.
base= top=
[ -z "$cwd" ] || base=$(git -C "$cwd" rev-parse --show-prefix 2>/dev/null) || base=$cwd
[ -z "$cwd" ] || top=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null) || top=
base=${base:-.}
# jq prelude of both filters below: $tops (the repo top, also without or with /private, longest first)
# and rel, which turns "<top>/" into "./" and a bare <top> into ".". Never into "": a path glued to a
# flag (-I<top>/src) must keep the slash before its first segment.
rel='($t | if . == "" then [] else [., (if startswith("/private/") then .[8:] else "/private" + . end)] end
    | sort_by(-length)) as $tops
  | def rel: reduce $tops[] as $x (.; split($x + "/") | join("./") | split($x) | join("."));'

case "$tool" in
  Bash) route=shell ;;
  view_image|skillsread|mcp__*|read_mcp_resource|list_mcp_resources|list_mcp_resource_templates) route=fields ;;
  apply_patch|Edit|Write) route=patch ;;
  *) exit 0 ;;
esac
if [ "$route" = fields ] && [ -n "$agent_id" ] && [ "$tool" != view_image ] && [ "$tool" != skillsread ]; then
  case "$agent_type" in implementer|spec_tester) echo "Blocked: MCP tools are closed to this agent. Read and search named folders with the file tools or the shell." >&2; exit 2 ;; esac
fi
if [ "$route" != patch ]; then
  [ -n "$agent_id" ] || exit 0
  call=$(printf '%s' "$input" | jq --arg b "$base" --arg t "$top" --arg n "$tool" "$rel"'
  .cwd = $b | .tool_input |= walk(if type == "string" then rel else . end)
  | if $n == "skillsread" then .tool_input = {paths: [.tool_input | .. | strings]} else . end' 2>/dev/null) || fail "cannot rewrite cwd"
else
  call=$(printf '%s' "$input" | jq --arg b "$base" --arg t "$top" "$rel"'
    {tool_input: {paths: [ (.tool_input.command // .tool_input.patch // "") | strings | splits("\n")
      | capture("^\\*\\*\\* (Add File|Update File|Delete File|Move to): (?<p>.+)$").p | sub("[ \t\r]+$"; "")
      | if startswith("/") then rel else $b + "/" + . end ]}}' 2>/dev/null) || fail "cannot read the patch headers"
fi
# Every caller: an installed kit file changes only by upgrade (the same list as the Claude wall),
# matched on repo-relative paths only: a user-level ~/.codex/agents/implementer.toml is not a kit file.
# The header paths come out as ./x (absolute in the repo) or <base>/x (relative); outside a git repo
# base is the cwd, so the repo top is the cwd. Any other absolute path is dropped (not a kit path).
# A shell write is left to seal-check (kit-changed).
if [ "$route" = patch ]; then
  kit='^(\.agents/skills/(dev-loop|pause|continue|release-audit|garden|release|release-notes)|\.claude/skills/(dev-loop|pause|continue|release-audit|garden|release|release-notes)|\.dev-loop)(/|$)|^\.codex/rules/dev-loop\.rules$|^\.claude/agents/(implementer|spec-tester)\.md$|^\.codex/agents/(final-reviewer|implementer|planner|security-reviewer|spec-tester|task-reviewer)\.toml$'
  normalize() { tr 'A-Z' 'a-z' | sed -e 's|//*|/|g' -e 's|/\./|/|g' -e ':a' -e 's|[^/][^/]*/\.\./||g' -e 'ta'; }
  repo_rel() { awk -v t="$1" 'BEGIN { p = index(t, "/private/") == 1 ? substr(t, 9) : "/private" t }
    /^\// { if (t != "" && index($0, t "/") == 1) $0 = substr($0, length(t) + 2)
            else if (t != "" && index($0, p "/") == 1) $0 = substr($0, length(p) + 2)
            else next }
    { sub(/^(\.\/)+/, ""); print }'; }
  kit_top=$(printf '%s' "${top:-$cwd}" | sed 's|/*$||' | normalize)
  if printf '%s' "$call" | jq -r '.tool_input.paths[]' | normalize | repo_rel "$kit_top" | grep -Eq "$kit"; then
    echo "Blocked: installed dev-loop kit file. Kit files change only by upgrade: change dev-loop, then run upgrade." >&2
    exit 2
  fi
fi
verdict=$(printf '%s' "$call" | sh "$(dirname "$0")/deny-path.sh" "$@") || exit 2
if [ -n "$verdict" ]; then
  reason=$(printf '%s' "$verdict" | jq -r '.hookSpecificOutput.permissionDecisionReason' 2>/dev/null) \
    || fail "unreadable deny-path verdict"
  echo "$reason" >&2
  exit 2
fi
word='(^|[^[:alnum:]_.-])([efr]?grep|rg|find)([^[:alnum:]_.-]|$)'
main=$KIT_MAIN
[ -n "$main" ] || main=$(git -C "$cwd" worktree list --porcelain 2>/dev/null | awk 'NR==1{w=substr($0,10)} /^bare$/{w=""} /^$/{exit} END{print w}')
cli=${KIT_ROOT:-$main}/.agents/skills/dev-loop/scripts/devloop.py
if [ "$tool" = Bash ] && [ -n "$agent_id" ] && [ "$agent_type" != spec_tester ] && [ -n "$top" ] && [ -f "$cli" ] \
  && field .tool_input.command | grep -Eq "$word"; then
  out=$(printf '%s' "$input" | python3 -I "$cli" search-wall 2>/dev/null) || fail "search check failed"
  printf '%s' "$out" | jq -e 'type == "object"' >/dev/null 2>&1 || fail "search check failed"
  if [ "$(printf '%s' "$out" | jq -r '.deny')" = true ]; then
    printf '%s\n' "$(printf '%s' "$out" | jq -r '.reason')" >&2
    exit 2
  fi
fi
exit 0
