#!/bin/bash
# PreToolUse(Bash) wall: the git push rule, git reset --hard and rm -rf /.
# Codex copy of claude/hooks/git-guard.sh: only deny() and ask() differ, because Codex
# parses permissionDecision "ask" but does not support it (the call would run), so every
# ask becomes a deny that names the rule.
IN=$(cat)
CMD=$(printf '%s' "$IN" | jq -r '.tool_input.command // empty')
CWD=$(printf '%s' "$IN" | jq -r '.cwd // empty')
[ -z "$CMD" ] && exit 0

# Any global options may sit before the subcommand: -C or -c take a value that may
# be quoted and may hold spaces, the rest (--no-pager, --git-dir=x) are one token.
G='git( +-[Cc] +("[^"]+"|'"'"'[^'"'"']+'"'"'|[^ ]+)| +-[^ ]+)* +'

deny() { jq -n --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'; exit 0; }
has()  { printf '%s' "$CMD" | grep -Eq "$1"; }

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
# a segment counts (if, timeout, xargs), and every quoted string is checked as a
# command of its own (sh -c, eval, ssh). Fail closed: a command the parser cannot
# read, an argument the shell would expand ($VAR, $(...), globs), arguments fed by
# xargs or find, a push after a command in the same line that moves HEAD or changes
# refs or config, or a parser crash, asks.
ask() { deny "Global rule: $1 needs explicit permission each time. Codex hooks cannot ask, so ask the user in chat and let them run it."; }
if has '(^|[^[:alnum:]_.-])git([^[:alnum:]_.-]|$)'; then
  REASON=$(CMD="$CMD" CWD="$CWD" python3 - <<'PY'
import os, re, shlex, subprocess, sys

FORCE = {"--force", "--force-with-lease", "--force-if-includes", "--delete", "--mirror",
         "--all", "--branches", "--prune", "--tags", "--follow-tags"}
PUSH_ARG = {"--push-option", "--repo", "--receive-pack", "--exec"}
GLOBAL_ARG = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
# Earlier commands in the same line that change what a later push resolves to.
MOVES_HEAD = {"checkout", "switch", "reset", "rebase", "worktree", "init", "clone", "branch"}
CHANGES_REFS = {"config", "remote", "tag", "update-ref", "symbolic-ref"}
FEEDERS = {"xargs", "parallel", "find", "fd"}
# chr(96) is a backtick. A literal one breaks bash parsing the $( ) around this script.
EXPANDS = set("${}*?[~" + chr(96))
PUNCT = ";&|()" + chr(96)
START = os.environ["CWD"]
# Git refuses an alias that shadows a builtin. Empty when git cannot list them, so
# every command from an unknown folder asks.
_r = subprocess.run(["git", "--list-cmds=builtins"], capture_output=True, text=True)
BUILTINS = set(_r.stdout.split()) if _r.returncode == 0 else set()


def ask(reason):
    print(reason)
    sys.exit(0)


def resolve(bases, to):
    # Every place a cd or -C can land, None for one the parser cannot follow (cd $X,
    # cd -). A ~ expands only at the start of a token: a path may hold ~
    # characters (iCloud~md~obsidian).
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
    # replaced by one word character, so "a"# is still no comment.
    bare = QUOTED.sub("x", cmd.replace("\n", ";"))
    if "<<" in bare or re.search(r"(^|[\s;&|()])#", bare) or re.search(r"(^|[\s;&|(" + chr(96) + r"])case\s", bare):
        return True
    # More operator characters than the bare text holds: a quoted or escaped one
    # became an operator.
    return sum(len(sep) for _, sep in segs) > sum(bare.count(c) for c in PUNCT)


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
            ask("a tag push")
        if not dst:
            ask("a push from a detached HEAD")
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


def check(cmd, cwds, drift):
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
        # Unbalanced quotes, often a heredoc body.
        ask("a git command the guard cannot parse")
    subshells, seen, lost, prev = [], set(cwds), unreliable(cmd, segs), ""
    for seg, sep in segs:
        for tok in seg:
            if any(c in tok for c in " \t;&|"):
                check(tok, cwds, drift)
        # NAME=value words before a cd do not stop it from running. CDPATH, HOME
        # or OLDPWD change where it lands, so its target becomes unknown.
        words, moved = seg, False
        while words and re.match(r"[A-Za-z_][A-Za-z0-9_]*=", words[0]):
            moved |= words[0].split("=", 1)[0] in ("CDPATH", "HOME", "OLDPWD")
            words = words[1:]
        if words and words[0] in ("cd", "pushd", "popd"):
            # A bare pushd, an option (cd -P) and a stack move (pushd +1) land in a
            # folder the guard cannot read.
            to = words[1] if len(words) > 1 else "~" if words[0] == "cd" else "+"
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


check(os.environ["CMD"], {os.environ["CWD"]}, set())
PY
) || REASON="a push the guard failed to check"
  [ -n "$REASON" ] && ask "$REASON"
fi
exit 0
