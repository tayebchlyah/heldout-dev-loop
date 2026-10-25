#!/bin/bash
# PreToolUse(Bash) wall: the git push rule, git reset --hard and rm -rf /.
# Codex copy of claude/hooks/git-guard.sh: only deny() and ask() differ, because Codex
# parses permissionDecision "ask" but does not support it (the call would run), so every
# ask becomes a deny that names the rule.
IN=$(cat)
# Fail closed (D8): no jq, or input that is not a JSON object, blocks the call.
fail() { echo "Blocked: guard error: $1" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || fail "jq is missing"
[ -n "$IN" ] || fail "empty hook input"
printf '%s' "$IN" | jq -e 'type == "object"' >/dev/null 2>&1 || fail "hook input is not a JSON object"
CMD=$(printf '%s' "$IN" | jq -r '.tool_input.command // empty' 2>/dev/null) || fail "command extraction failed"
CWD=$(printf '%s' "$IN" | jq -r '.cwd // empty' 2>/dev/null) || fail "cwd extraction failed"
AGENT=$(printf '%s' "$IN" | jq -r '.agent_type // empty' 2>/dev/null) || fail "agent_type extraction failed"
[ -z "$CMD" ] && exit 0

# Any global options may sit before the subcommand: -C or -c take a value that may
# be quoted and may hold spaces, the rest (--no-pager, --git-dir=x) are one token.
G='git( +-[Cc] +("[^"]+"|'"'"'[^'"'"']+'"'"'|[^ ]+)| +-[^ ]+)* +'

deny() { jq -n --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'; exit 0; }
has()  { printf '%s' "$CMD" | grep -Eq -e "$1"; }
# Claude's deny() does the same; block() is in both guards so the seal rules stay
# identical (parity), and Codex too denies them with exit 2 and a stderr line.
block() { echo "$1" >&2; exit 2; }

# 0a. Only spec-tester seals (D5, D10). A typed seal or patch trailer is denied on the raw text,
# a heredoc message included: the key, : or = and a value; a --trailer with that key (any
# separator); git interpret-trailers naming it. Prose that names the trailer passes.
TESTER=spec_tester
KEY='Dev-Loop-Seal(-Patch)?'
if [ "$AGENT" != "$TESTER" ] && { has "$KEY[[:space:]]*[:=][[:space:]]*[^[:space:]]" \
    || has "--trailer[=[:space:]]+['\"]?$KEY([^[:alnum:]_-]|\$)" || { has interpret-trailers && has "$KEY"; }; }; then
  block "Blocked: only devloop.py seal, run by spec-tester, writes the Dev-Loop-Seal trailer."
fi

# A heredoc body is text, not commands: every check below reads the line without it.
# A body whose line names a shell (bash, sh, zsh, ssh, eval) stays. If this step
# fails, the checks read the whole text.
case "$CMD" in *'<<'*) BODYLESS=$(CMD="$CMD" python3 - <<'PY'
import os, re

RUNNERS = {"sh", "bash", "zsh", "eval", "ssh", "su"}
HEREDOC = re.compile(r"(?<!<)<<(?!<)(-?)\s*(['\"]?)([A-Za-z_][\w.-]*)\2")


def strip_heredocs(cmd):
    # Drops each heredoc body and its end line, so prose with apostrophes or git words
    # is not read as commands. A body whose line names a shell stays. A heredoc with
    # no end line keeps the rest of the text.
    lines, out, i = cmd.split("\n"), [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        feeds = any(os.path.basename(w) in RUNNERS for w in re.split(r"[\s;&|()]+", line))
        for dash, _, end in HEREDOC.findall(line):
            j = i
            while j < len(lines) and (lines[j].lstrip("\t") if dash else lines[j]) != end:
                j += 1
            if j == len(lines):
                return "\n".join(out + lines[i:])
            if feeds:
                out += lines[i:j + 1]
            i = j + 1
    return "\n".join(out)


print(strip_heredocs(os.environ["CMD"]), end="")
PY
) && CMD=$BODYLESS || RAW=1 ;; esac

# 0b. Who runs the seal (D5, D11), on the text without heredoc bodies. The spec-tester's commit
# counts in command position only: the line start or after ; & | (. Any other caller: a
# command segment with a shell word seal (quotes removed, as the shell does) after a word
# that names devloop, whatever wraps the run (env, python3 -I, uv run). A segment that starts
# with a read-only tool (grep, cat, git log) passes, as do "devloop.py seal" and seal-check.
RUN='(^|[;&|(])[[:space:]]*'
if [ "$AGENT" = "$TESTER" ]; then
  has "${RUN}${G}commit([^[:alnum:]_-]|\$)" && block "Blocked: spec-tester commits only through devloop.py seal: python3 .agents/skills/dev-loop/scripts/devloop.py seal --feature <slug> --message <subject>."
else
  # Exit 1 (also a crash or unbalanced quotes) means a seal run: fail closed.
  case "$CMD" in *devloop*seal*) printf '%s' "$CMD" | python3 -c 'import shlex, sys
PUNCT = ";&|()<>" + chr(96)
READ = {"grep", "rg", "cat", "less", "head", "tail", "ls", "find", "echo", "printf"}
lex = shlex.shlex(sys.stdin.read().replace("\n", ";"), posix=True, punctuation_chars=PUNCT)
lex.whitespace_split, lex.commenters = True, ""
seg = []
for tok in [*lex, ";"]:
    if not all(c in PUNCT for c in tok):
        seg.append(tok)
        continue
    run = next((i for i, w in enumerate(seg) if "devloop" in w), None)
    read = seg[:1] and (seg[0] in READ or seg[:2] == ["sed", "-n"]
                        or seg[0] == "git" and seg[1:2] in (["grep"], ["log"], ["show"], ["diff"]))
    if run is not None and not read and "seal" in seg[run + 1:]:
        sys.exit(1)
    seg = []' 2>/dev/null || block "Blocked: only spec-tester may run devloop.py seal." ;; esac
fi

# 1. rm -rf on root only.
has 'rm +-[rf]+ +/( |$)|rm +-[rf]+ +/\*' && deny "Blocked: rm -rf / is never allowed."

# 2. Hard reset destroys work.
has "${G}reset +--hard" && deny "Blocked: git reset --hard discards work. Use git stash or --soft."

# 3. A plain push of a feature branch runs. A push that rewrites, deletes or publishes
# beyond one feature branch asks: force, a + refspec, delete, mirror, all, prune, tags,
# main or master as target, a bare push while main, master or a detached HEAD is
# checked out, or a bare push whose remote config can reach other branches.
# Parsed with shlex, not regex, so quotes, combined short flags (-uf), global options
# (-c, -C, --git-dir), cd and git aliases do not walk past the rule. A git anywhere in
# a segment counts (if, timeout, xargs). Only what the shell runs is checked: a heredoc
# body is text unless its line feeds it to a shell, and a quoted string is a command
# only in a segment that runs a shell (sh -c, eval, ssh, su -c). Fail closed: a push
# line the parser cannot read, an argument the shell would expand ($VAR, $(...),
# globs), arguments fed by xargs or find, a push after a command in the same line that
# moves HEAD or changes refs or config, or a parser crash, asks.
# A release tag push and gh release create also carry the release-check verdict (D14),
# $2: "; release-check: ..." or empty. A subagent is denied without running the check.
AGENT_ID=$(printf '%s' "$IN" | jq -r '.agent_id // empty')
ask() { deny "Global rule: $1 needs explicit permission each time$2. Codex hooks cannot ask, so ask the user in chat and let them run it."; }
if has '(^|[^[:alnum:]_.-])(git|gh)([^[:alnum:]_.-]|$)'; then
  REASON=$(CMD="$CMD" CWD="$CWD" RAW="$RAW" AGENT_ID="$AGENT_ID" python3 - <<'PY'
import fnmatch, json, os, re, shlex, subprocess, sys

FORCE = {"--force", "--force-with-lease", "--force-if-includes", "--delete", "--mirror",
         "--all", "--branches", "--prune", "--tags", "--follow-tags"}
PUSH_ARG = {"--push-option", "--repo", "--receive-pack", "--exec"}
GLOBAL_ARG = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
# Earlier commands in the same line that change what a later push resolves to.
MOVES_HEAD = {"checkout", "switch", "reset", "rebase", "worktree", "init", "clone", "branch"}
CHANGES_REFS = {"config", "remote", "tag", "update-ref", "symbolic-ref"}
FEEDERS = {"xargs", "parallel", "find", "fd"}
# gh release create options that take a value.
GH_ARG = {"--target", "-t", "--title", "-n", "--notes", "-F", "--notes-file", "--discussion-category",
          "--notes-start-tag", "-R", "--repo"}
# Commands that run a string or a heredoc body as shell commands.
RUNNERS = {"sh", "bash", "zsh", "eval", "ssh", "su"}
# chr(96) is a backtick. A literal one breaks bash parsing the $( ) around this script.
EXPANDS = set("${}*?[~" + chr(96))
PUNCT = ";&|()" + chr(96)
START = os.environ["CWD"]
# Git refuses an alias that shadows a builtin. Empty when git cannot list them, so
# every command from an unknown folder asks.
_r = subprocess.run(["git", "--list-cmds=builtins"], capture_output=True, text=True)
BUILTINS = set(_r.stdout.split()) if _r.returncode == 0 else set()


def ask(reason, suffix=""):
    # The rule on the first line, the release-check text (one line) on the second.
    print(reason + ("\n" + suffix if suffix else ""))
    sys.exit(0)


# Variables the line surely set to an absolute path before this point.
VARS = {}


def expand(to):
    # $NAME, ${NAME} or $NAME/x, for a NAME in VARS.
    m = re.fullmatch(r"\$(\w+|\{\w+\})(/.*)?", to)
    name = m and m.group(1).strip("{}")
    return VARS[name] + (m.group(2) or "") if name in VARS else to


def record(words):
    # A segment of only NAME=/abs words (after an optional export) sets shell
    # variables. Kept only when the line sets NAME once and uses it in no other way
    # than $NAME or ${NAME}: no for NAME, read NAME, unset NAME, NAME+=.
    for w in words:
        name, value = w.split("=", 1)
        line = os.environ["CMD"]
        once = (len(re.findall(rf"(?<![\w$]){name}=", line)) == 1
                and not re.search(rf"(?<![\w${{]){name}(?![\w=}}])", line))
        # A ~ after a word character is text (iCloud~md~obsidian). An assignment
        # also expands a ~ after : or =, so any other ~ makes the value unknown.
        if (once and os.path.isabs(value) and not (EXPANDS - {"~"}) & set(value)
                and not re.search(r"(?<!\w)~", value)):
            VARS[name] = value


def resolve(bases, to):
    # Every place a cd or -C can land, None for one the parser cannot follow (cd $X
    # for an X not in VARS, cd -). A ~ expands only at the start of a token: a path
    # may hold ~ characters (iCloud~md~obsidian).
    to = expand(to)
    target = os.path.expanduser(to)
    if to == "-" or (EXPANDS - {"~"}) & set(to) or target.startswith("~"):
        return {None}
    if os.path.isabs(target):
        return {target}
    return {None if b is None else os.path.join(b, target) for b in bases}


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def segments(cmd):
    # Yields each command with the operator that follows it. An operator with no
    # command before it, such as an opening (, comes with an empty one. No comment
    # syntax: a # must not hide the rest of the line from the guard.
    lex = shlex.shlex(cmd.replace("\n", ";"), posix=True, punctuation_chars=PUNCT)
    lex.whitespace_split = True
    lex.commenters = ""
    seg = []
    for tok in lex:
        if tok and all(c in PUNCT for c in tok):
            yield seg, tok
            seg = []
        else:
            seg.append(tok)
    if seg:
        yield seg, ""


# A backslash escape, a single-quoted string, a double-quoted string.
QUOTED = re.compile(r"\\.|'[^']*'|\"(?:[^\"\\]|\\.)*\"")


def unreliable(cmd, segs):
    # segments() reads punctuation inside quotes or after a backslash as an operator,
    # a comment and a heredoc body as commands, and a case pattern's ) as the end of
    # a subshell. True when any of these can sit in cmd. Quoted and escaped text is
    # replaced by one word character, so "a"# is still no comment. A heredoc body is
    # left only on a line that feeds a shell, or when the first pass failed (RAW).
    bare = QUOTED.sub("x", cmd.replace("\n", ";"))
    body = "<<" in bare and (os.environ["RAW"] or RUNNERS & {os.path.basename(w) for w in re.split(r"[\s;&|()]+", bare)})
    if body or re.search(r"(^|[\s;&|()])#", bare) or re.search(r"(^|[\s;&|(" + chr(96) + r"])case\s", bare):
        return True
    # More operator characters than the bare text holds: a quoted or escaped one
    # became an operator.
    return sum(len(sep) for _, sep in segs) > sum(bare.count(c) for c in PUNCT)


def release_suffix(repo, args):
    # The release-check verdict of the main worktree's CLI, 3 s of the hook's 5.
    if os.environ["AGENT_ID"]:
        return ""
    main = git(repo, "worktree", "list", "--porcelain")[1].partition("\n")[0].removeprefix("worktree ")
    cli = os.path.join(main, ".agents", "skills", "dev-loop", "scripts", "devloop.py")
    if not main or not os.path.isfile(cli):
        return "; release-check failed: no CLI"
    try:
        r = subprocess.run(["python3", cli, "release-check", "--repo", repo, *args],
                           capture_output=True, text=True, errors="replace", timeout=3)
    except subprocess.TimeoutExpired:
        return "; release-check failed: timeout"
    out = None
    try:
        out = json.loads(r.stdout)
        if out["ok"] is True:
            return "; release-check: ok"
        if out["reasons"]:
            return "; release-check: " + ", ".join(x["reason"] for x in out["reasons"])
    except (ValueError, TypeError, KeyError):
        pass
    error = out.get("error") if isinstance(out, dict) else None
    return "; release-check failed: " + (r.stderr.strip().splitlines() or [str(error or f"exit {r.returncode}")])[0]


def check_release(args, cwds):
    # gh release create tags a local tag of that name, else --target, else what
    # origin/main points at. Any tag name asks: a release is always the user's.
    tag, target, j = None, None, 0
    while j < len(args):
        a = args[j]
        if a.split("=", 1)[0] == "--target":
            target = a.split("=", 1)[1] if "=" in a else (args[j + 1] if j + 1 < len(args) else "")
        if a in GH_ARG:
            j += 1
        elif not a.startswith("-") and tag is None:
            tag = a
        j += 1
    if None in cwds or any(EXPANDS & set(v) for v in (tag or "", target or "")):
        ask("a release create", "; release-check failed: a folder or argument the guard cannot read")
    repo = min(cwds)
    rc, sha = git(repo, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}") if tag else (1, "")
    if rc and target is not None:
        rc, sha = git(repo, "rev-parse", "--verify", "--quiet", f"{target}^{{commit}}")
        if rc:
            ask("a release create", f"; release-check failed: target {target} does not resolve")
    elif rc:
        rc, sha = git(repo, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/main^{commit}")
        if rc:
            ask("a release create", "; release-check failed: refs/remotes/origin/main does not resolve")
    ask(f"a release create at {sha}", release_suffix(repo, ["--sha", sha]))


def gh_release(args, cwds):
    # gh [flags] release [flags] create|new: gh flags may sit around release, -R and
    # --repo take a value. A word the shell expands after release asks.
    words, i = [], 0
    while i < len(args) and len(words) < 2:
        a = args[i]
        if words and EXPANDS & set(a):
            ask("a gh release command", "; release-check failed: a gh release command the guard cannot read")
        if a in ("-R", "--repo"):
            i += 1
        elif not a.startswith("-"):
            words.append(a)
            if words[0] != "release":
                return
        i += 1
    if words == ["release", "create"] or words == ["release", "new"]:
        check_release(args[i:], cwds)


def tag_names(repo, specs, branch):
    # Every tag name the push writes on the remote: the destination of x:refs/tags/v1,
    # a local tag pushed by name, tag <name>. A wildcard counts as many.
    names, j = set(), 0
    while j < len(specs):
        spec = specs[j].lstrip("+")
        if spec == "tag" and j + 1 < len(specs):
            j += 1
            spec = "refs/tags/" + specs[j]
        if "*" in spec:
            return {"*", "**"}
        src, _, dst = spec.partition(":")
        if src in ("HEAD", "@"):
            src = branch
        dst = dst or src
        if dst.startswith("refs/tags/"):
            names.add(dst.removeprefix("refs/tags/"))
        elif src and git(repo, "show-ref", "--verify", "--quiet", f"refs/tags/{src.removeprefix('refs/tags/')}")[0] == 0:
            names.add(dst)
        j += 1
    return names


def check_push(repo, args, drift):
    if any(EXPANDS & set(a) for a in args):
        ask("a push with an argument the shell expands")
    if "refs" in drift:
        ask("a push after a command that changes refs or config")
    pos, j = [], 0
    while j < len(args):
        a = args[j]
        if a == "--":
            pos += args[j + 1:]
            break
        if a.startswith("--"):
            name = a.split("=", 1)[0]
            if name in FORCE:
                ask("a force, delete, mirror, all, prune or tag push")
            if name in PUSH_ARG and "=" not in a:
                j += 1
        elif a.startswith("-") and len(a) > 1:
            if "f" in a[1:] or "d" in a[1:]:
                ask("a force or delete push")
            if a.endswith("o"):
                j += 1
        else:
            pos.append(a)
        j += 1
    _, branch = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    remote, specs = (pos[0] if pos else ""), pos[1:]
    if "head" in drift and (not specs or any(s.partition(":")[0] in ("HEAD", "@") for s in specs)):
        ask("a push of HEAD after a command that moves it")
    if not specs:
        r = remote or git(repo, "config", f"branch.{branch}.remote")[1] or "origin"
        if (git(repo, "config", "--get-all", f"remote.{r}.push")[1]
                or git(repo, "config", f"remote.{r}.mirror")[1] == "true"
                or git(repo, "config", "push.default")[1] == "matching"
                or git(repo, "config", f"branch.{branch}.merge")[1].removeprefix("refs/heads/") in ("main", "master")):
            ask("a bare push whose remote config can reach other branches")
        specs = ["HEAD"]
    for spec in specs:
        if spec.startswith(("+", ":")) or "*" in spec:
            ask("a forced, deleting or wildcard refspec")
        src, _, dst = spec.partition(":")
        if src in ("HEAD", "@"):
            src = branch
        dst = dst or src
        if (src.startswith("refs/tags/") or dst.startswith("refs/tags/")
                or (src and git(repo, "show-ref", "--verify", "--quiet", f"refs/tags/{src}")[0] == 0)):
            names = tag_names(repo, specs, branch)
            one = len(names) == 1 and fnmatch.fnmatchcase(next(iter(names)), "v*")
            ask("a tag push", release_suffix(repo, ["--tag", *names]) if one else "")
        if not dst:
            ask("a push from a detached HEAD" if git(repo, "rev-parse", "--git-dir")[0] == 0
                else "a push from an unknown repository")
        if dst.removeprefix("refs/heads/") in ("main", "master"):
            ask("a push to main or master")


def check_git(args, cwds, drift, fed):
    repos, i, config, gitdir = cwds, 0, [], False
    while i < len(args) and args[i].startswith("-"):
        # --git-dir and --work-tree point git at a repo the guard does not resolve.
        gitdir |= args[i].split("=", 1)[0] in ("--git-dir", "--work-tree")
        if args[i] in GLOBAL_ARG and i + 1 < len(args):
            if args[i] == "-C":
                repos = resolve(repos, args[i + 1])
            if args[i] in ("-c", "--config-env"):
                config.append(args[i + 1])
            i += 1
        elif args[i].startswith("--config-env="):
            config.append(args[i])
        i += 1
    sub = args[i] if i < len(args) else ""
    if gitdir:
        repos = repos | {None}
    if sub == "send-pack" or (sub == "subtree" and "push" in args[i + 1:]):
        ask("a send-pack or subtree push")
    if sub != "push":
        # The alias may live in any repo the line can be in. From a folder the guard
        # cannot follow, any command that may be an alias asks.
        if None in repos and sub and sub not in BUILTINS:
            ask("a git command from a folder the guard cannot follow")
        aliases = [c for c in config if c.startswith("alias.")]
        if sub and "push" in " ".join(aliases + [git(START if r is None else r, "config", "--get", f"alias.{sub}")[1]
                                                 for r in repos]):
            ask("a git alias that pushes")
        if sub in MOVES_HEAD:
            drift.add("head")
        if sub in CHANGES_REFS or config:
            drift.add("refs")
        return
    if fed:
        ask("a push whose arguments come from another command")
    if config:
        ask("a push with config overrides")
    if gitdir:
        ask("a push with --git-dir or --work-tree")
    if None in repos:
        ask("a push from a folder the guard cannot follow")
    for r in sorted(repos):
        check_push(r, args[i + 1:], drift)


def check(cmd, cwds, drift, top=False):
    # cwds holds every directory the line can be in at this point. A cd (or
    # pushd) replaces it only when all of these hold, the one case where the next
    # command surely runs in the new directory: the line start, "&&" or ";" comes
    # before it and "&&" follows; its target is absolute or starts with ~; cd is
    # the first word after any NAME=value words; its first argument is no option
    # (-P, --) or stack move (+1); and no HOME= sits anywhere in the line when the
    # target is ~ or ~/x. Else the old ones stay possible, plus None for a target
    # the guard cannot read: a cd before ";" may never have run (after && or ||,
    # inside if, a loop or a function body), true || cd x && git push is
    # (true || cd x) && git push, a push without the cd, and CDPATH, set in the
    # line or the user's profile, can send a relative cd anywhere. A cd that is
    # not the first word (builtin cd, if cd, eval cd) adds None.
    # A ( ), $( ) or backtick body is a subshell: its end adds the cwds from
    # before it back. A ) that closes no subshell means a stray ) (a case pattern,
    # an escaped paren) closed one too early, so the parse is unreliable: cwds
    # becomes every folder seen so far, and no later cd narrows it. The same holds
    # for the whole line when unreliable() finds a quote, escape, comment, heredoc
    # or case that can fool the parser: a cd then only adds its targets.
    try:
        segs = list(segments(cmd))
    except ValueError:
        # Unbalanced quotes. Only a line that may push asks.
        if re.search(r"(?<![\w.-])git(?![\w.-]).*(\bpush\b|send-pack)", cmd):
            ask("a push the guard cannot parse")
        return
    subshells, seen, lost, prev = [], set(cwds), unreliable(cmd, segs), ""
    for seg, sep in segs:
        if any(os.path.basename(w) in RUNNERS for w in seg):
            for tok in seg:
                if any(c in tok for c in " \t;&|"):
                    check(tok, cwds, drift)
        # NAME=value words before a cd do not stop it from running. CDPATH, HOME
        # or OLDPWD change where it lands, so its target becomes unknown.
        words, moved = seg, False
        # An assignment surely ran before the next command only at the line start or
        # after ";", outside a subshell and a shell script (sh -c), on a line the
        # parser reads reliably.
        sets = seg[1:] if seg[:1] == ["export"] else seg
        if sets and all(re.match(r"[A-Za-z_][A-Za-z0-9_]*=", w) for w in sets) \
                and prev in ("", ";") and top and not subshells and not lost:
            record(sets)
        while words and re.match(r"[A-Za-z_][A-Za-z0-9_]*=", words[0]):
            moved |= words[0].split("=", 1)[0] in ("CDPATH", "HOME", "OLDPWD")
            words = words[1:]
        if words and words[0] in ("cd", "pushd", "popd"):
            # A bare pushd, an option (cd -P) and a stack move (pushd +1) land in a
            # folder the guard cannot read.
            to = expand(words[1]) if len(words) > 1 else "~" if words[0] == "cd" else "+"
            # A HOME= anywhere in the line may have moved ~ before this cd.
            moved |= "HOME=" in os.environ["CMD"] and (to == "~" or to.startswith("~/"))
            new = {None} if words[0] == "popd" or moved or to.startswith(("-", "+")) else resolve(cwds, to)
            # A relative target never narrows. CDPATH, set earlier in the line or in
            # the user's profile, can send it elsewhere, except after ./ or ../.
            rooted = os.path.isabs(to) or to.startswith("~")
            if not rooted and to.split("/")[0] not in (".", ".."):
                new = new | {None}
            cwds = new if prev in ("", "&&", ";") and sep == "&&" and rooted and not lost else cwds | new
            seen |= cwds
        else:
            # A cd behind a wrapper (builtin, command, eval, if, {, !) is not
            # followed: the line may be anywhere after it.
            if any(w in ("cd", "pushd", "popd") for w in words[1:]):
                cwds = cwds | {None}
                seen |= cwds
            if any(t.startswith("GIT_") and "=" in t for t in seg):
                drift.add("refs")
            # Every git word counts, not only the first: in "sudo -u git git commit" the
            # first one is a user name.
            for k, tok in enumerate(seg):
                if os.path.basename(tok) == "git":
                    fed = any(os.path.basename(t) in FEEDERS or t in ("-exec", "-execdir") for t in seg[:k])
                    # env -C or --chdir before git runs it in a folder the guard does not follow.
                    chdir = any(t in ("-C", "--chdir") or t.startswith("--chdir=") for t in seg[:k])
                    check_git(seg[k + 1:], cwds | {None} if chdir else cwds, drift, fed)
                if os.path.basename(tok) == "gh":
                    gh_release(seg[k + 1:], cwds)
        for c in sep:
            if c == ")" or (c == chr(96) and subshells and subshells[-1][0] == c):
                # A union, not a swap: a ) that closes no subshell (a case pattern,
                # an escaped paren) must not drop a cd that ran.
                if subshells:
                    cwds = subshells.pop()[1] | cwds
                else:
                    cwds, lost = set(seen), True
            elif c in "(" + chr(96):
                subshells.append((c, cwds))
        prev = sep


check(os.environ["CMD"], {os.environ["CWD"]}, set(), top=True)
PY
) || REASON="a push the guard failed to check"
  NL='
'
  WHAT=${REASON%%"$NL"*}
  SUFFIX=${REASON#"$WHAT"}
  [ -n "$REASON" ] && ask "$WHAT" "${SUFFIX#"$NL"}"
fi
exit 0
