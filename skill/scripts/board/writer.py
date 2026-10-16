"""`devloop.py board`: write the status notes of this machine from local state (spec D6 to D14, D18)."""

from __future__ import annotations

import fcntl
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Callable

from . import sources, state, store

TIME = "%Y-%m-%dT%H:%M:%SZ"
STOP_WAITING = {2: "approve the spec", 3: "reply in the session", 4: "reply in the session",
                5: "reply in the session", 6: "reply in the session", 7: "review the PR"}
FEATURES = "features"
TEMPLATE = Path(__file__).with_name("board-note.md")


@dataclass(frozen=True)
class Context:
    """The values every note of one run shares."""
    dl: ModuleType
    repo: Path
    notes: Path
    folder: Path
    state: Path
    machine: dict
    project: str
    data: dict  # D1: the board state, loaded under the lock, saved once before it is released.
    claude: Path  # D10: the Claude config folder that holds the native task lists.


def skip(reason: str, feature: str | None = None) -> dict:
    return {"feature": feature, "reason": reason}


def inside(path: Path, root: Path) -> bool:
    a, b = str(path), str(root)
    if sys.platform == "darwin":
        a, b = a.casefold(), b.casefold()
    return a == b or a.startswith(b.rstrip("/") + "/")


def worktrees(dl: ModuleType, repo: Path) -> dict[Path, str | None]:
    """Each worktree path, resolved, to the slug of its branch `<prefix>/<slug>` (or of its rebase), else None."""
    found: dict[Path, str | None] = {}
    path: Path | None = None
    for line in (dl.git(repo, "worktree", "list", "--porcelain") or "").splitlines():
        if line.startswith("worktree "):
            path = Path(line[len("worktree "):]).resolve()
            found[path] = None
        elif path is None:
            continue
        elif line.startswith("branch refs/heads/") and "/" in line[len("branch refs/heads/"):]:
            found[path] = line.rsplit("/", 1)[1].lower()
        elif line == "detached" and "/" in (dl.rebasing(path) or ""):
            found[path] = (dl.rebasing(path) or "").rsplit("/", 1)[1].lower()
    return found


def read_slugs(path: Path) -> list[str]:
    try:
        claims = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    return [c for c in claims if isinstance(c, str)] if isinstance(claims, list) else []


def section(text: str, heading: str) -> str:
    found = re.search(rf"^## {heading}[ \t]*$(.*?)(?=^#{{1,2}} |\Z)", text, re.MULTILINE | re.DOTALL)
    return found.group(1).strip() if found else ""


def iso(stamp: float) -> str:
    return time.strftime(TIME, time.gmtime(stamp))


def tasks(plan: Path | None, ledger: Path | None, saved: dict, now: str, stage: int, doing: set[int],
          briefs: set[int], done_time: Callable[[int, str], str | None]) -> list[dict]:
    """D7, D9, D14: plan headings, done from the ledger `complete` lines, doing from the native list (`doing`), then
    the briefs, else at Stage 5 the first task not done. `saved`: the task times of the state entry, by `str(n)`;
    `done_time(n, line)`: the file time of a done task, else None."""
    if plan is None:
        return []
    titles: dict[int, str] = {}
    for n, title in re.findall(r"^#{2,3} Task (\d+)\b[:.]?\s*(.*)$", plan.read_text(), re.MULTILINE):
        titles.setdefault(int(n), title.strip())
    complete: dict[int, str] = {}
    if ledger is not None and ledger.is_file():
        for line in re.findall(r"^Task \d+: complete.*$", ledger.read_text(), re.MULTILINE):
            complete.setdefault(int(line.split(":", 1)[0][len("Task "):]), line)
    active = {n for n in titles if n not in complete and (n in doing or n in briefs)}
    if not active and stage == 5:
        active = {n for n in titles if n not in complete}
        active = {min(active)} if active else set()
    out: list[dict] = []
    previous_done = None
    for n in sorted(titles):
        seen = saved.get(str(n))
        seen = seen if isinstance(seen, dict) else {}
        task: dict = {"n": n, "title": titles[n], "state": "todo"}
        if n in complete or n in active:
            brief = sources.birth(ledger.parent / f"task-{n}-brief.md") if ledger is not None else None
            started = seen.get("started") or (iso(brief) if brief is not None else None) or (
                now if n in active else previous_done)
            if started:
                task["started"] = started
            if n in complete:
                task.update(state="done", done=seen.get("done") or done_time(n, complete[n]) or now)
            else:
                task["state"] = "doing"
        previous_done = task.get("done")
        out.append(task)
    return out


def merge(ctx: Context, entry: dict, slug: str, found: dict, spec: Path | None, now: str, waiting: str) -> dict:
    """The new status note of one feature from its detect result, its spec, plan and ledger, and its state entry
    (Board Robustness D1: never from the old note). `updated` is the caller's."""
    stage: int = found["stage"]
    times = dict(entry["stage_times"]) if isinstance(entry.get("stage_times"), dict) else {}
    seen_late = any(k.isdigit() and int(k) >= 3 for k in times)  # D18: a run already saw Stage 3 or later
    text = spec.read_text() if spec else ""
    title = re.search(r"^# (.+)$", text, re.MULTILINE)
    goal = re.split(r"\n\s*\n", section(text, "Goal"))[0]
    assumptions = [a.strip() for a in re.findall(r"^[-*] (.+)$", section(text, "Assumptions"), re.MULTILINE)]
    baseline = entry["assumptions_baseline"] if isinstance(entry.get("assumptions_baseline"), list) else []
    if stage >= 3 and not seen_late:
        baseline = assumptions
    plan = ctx.dl.find_plan(ctx.notes, spec) if spec else None
    worktree = found["worktree"]
    ledger = Path(worktree) / ".superpowers" / "sdd" / plan.stem / "progress.md" if plan and worktree else None
    saved = entry["tasks"] if isinstance(entry.get("tasks"), dict) else {}

    def done_time(n: int, line: str) -> str | None:
        """D7: the tests log time, else the commit time of the `complete` line."""
        at = sources.mtime(ledger.parent / f"task-{n}-tests.log") if ledger is not None else None
        if at is None and worktree:
            at = sources.commit_time(ctx.dl, Path(worktree), line)
        return iso(at) if at is not None else None

    doing = sources.native_doing(ctx.claude, state.session_of(ctx.data, slug))
    task_list = tasks(plan, ledger, saved, now, stage, doing, sources.briefs(ledger.parent) if ledger else set(),
                      done_time)
    # D6: each stage time is set once. Stage 5: the ledger birth time, none while the ledger is missing (a Track S
    # feature has no ledger: its first run time). Stage 6: the latest task done time once all tasks are done.
    if stage >= 5 and "5" not in times:
        born = sources.birth(ledger) if ledger else None
        if born is not None:
            times["5"] = iso(born)
        elif stage == 6 or (ledger is None and stage == 5):
            times["5"] = now
    if stage >= 6 and "6" not in times and task_list and all(t["state"] == "done" for t in task_list):
        times["6"] = max(t["done"] for t in task_list)
    if stage != 5:
        times.setdefault(str(stage), now)
    # A1: a Track S branch with commits ahead records its last commit, so a ship is seen once the branch is gone.
    head = ctx.dl.git(Path(worktree), "rev-parse", "HEAD") if spec is None and worktree and stage in (6, 7) else None
    return {
        "type": "dev-loop-status",
        "schema": 1,
        "project": ctx.project,
        "feature": slug,
        "title": title.group(1).strip() if title else slug,
        "description": " ".join(line.strip() for line in goal.splitlines()),
        "machine_id": ctx.machine["id"],
        "owner": ctx.machine["owner"],
        "machine": ctx.machine["machine"],
        "track": found["track"] or "",
        "acceptance": ctx.dl.frontmatter(spec).get("acceptance", "").lower() if spec else "",
        "stage": stage,
        "stage_times": times,
        "tasks": task_list,
        "waiting": waiting,
        "decisions": [a for a in assumptions if a not in baseline] if stage >= 3 or seen_late else [],
        "assumptions_baseline": baseline,
        **({"head": head} if head else {}),
        "shipped_at": "",
        "backfilled": bool(entry.get("backfilled")),
        "updated": "",
    }


def remembered_tasks(entry: dict, task_list: list[dict]) -> dict:
    """D7: the saved task times with this run's times merged in, so a heading removed then restored keeps its times."""
    saved = entry.get("tasks")
    merged = {n: dict(t) for n, t in saved.items() if isinstance(t, dict)} if isinstance(saved, dict) else {}
    for t in task_list:
        times = {k: t[k] for k in ("started", "done") if k in t}
        if times:
            merged[str(t["n"])] = {**merged.get(str(t["n"]), {}), **times}
    return merged


def remember(entry: dict, fields: dict) -> None:
    """D1: keep in the state entry the values a later run must not read back from the note."""
    entry.update(stage_times=fields["stage_times"], assumptions_baseline=fields["assumptions_baseline"],
                 waiting=fields["waiting"], backfilled=fields["backfilled"],
                 tasks=remembered_tasks(entry, fields["tasks"]))
    if "head" in fields:
        entry["head"] = fields["head"]
    entry["fields_digest"] = state.digest(fields)


def run(dl: ModuleType, repo: Path, notes: Path, home: Path, *, event: str | None, hook_cwd: Path | None,
        hook_session: str | None, claim: str | None) -> tuple[int, dict]:
    payload: dict = {"notes": [], "removed": [], "board_note": None, "skipped": []}
    top = dl.git(repo, "rev-parse", "--show-toplevel")
    if top is None or not (Path(top) / "dev-loop.json").is_file():
        payload["skipped"].append(skip("no-dev-loop"))
        return 0, payload
    # Stage 0 (the conditions of `detect`, without its scan of every spec): no HEAD, or no glossary.
    glossary = any((Path(top) / name).is_file() for name in ("GLOSSARY.md", "CONTEXT.md"))
    if dl.git(repo, "rev-parse", "HEAD") is None or not glossary:
        payload["skipped"].append(skip("stage-0"))
        return 0, payload
    common = Path(dl.git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    state_dir = common / "dev-loop"
    now = time.strftime(TIME, time.gmtime())
    if any((notes / "specs").glob(".*.icloud")):  # D5: an evicted spec would drop claims and notes.
        payload["skipped"].append(skip("placeholder"))
        state.save_last_run(state_dir, now, payload["skipped"])
        return 0, payload
    specs = dict(dl.specs(notes))
    claim = claim.lower() if claim else None
    if claim and claim not in specs:
        return 1, {"error": f"no spec for {claim} in {notes / 'specs'}"}

    trees = worktrees(dl, repo)
    notes_real = notes.resolve()
    if any(inside(notes_real, root) for root in [*trees, common]):
        payload["skipped"].append(skip("notes-in-repo"))
        state.save_last_run(state_dir, now, payload["skipped"])
        return 0, payload
    state_dir.mkdir(parents=True, exist_ok=True)
    queue = state_dir / "board-events.jsonl"
    with open(state_dir / "board.lock", "a+") as lock:
        # DEVLOOP_TEST_CLAIM_WAIT: tests only, a shorter D7 claim wait than the spec's 10 s.
        if not take(lock, float(os.environ.get("DEVLOOP_TEST_CLAIM_WAIT") or 10) if claim else 0):
            if claim:  # D7: a claim is never dropped silently.
                return 1, {"error": "board lock busy, claim not saved"}
            # D2, D13: the lock holder applies it; a run without an event queues `null`, a refresh.
            line = json.dumps({"event": event, "cwd": str(hook_cwd or repo), "session_id": hook_session,
                               "at": time.strftime(TIME, time.gmtime())})
            fd = os.open(queue, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
            with os.fdopen(fd, "a") as handle:
                handle.write(line + "\n")
            payload["skipped"].append(skip("busy"))
            return 0, payload
        found: dict[str, dict] = {}

        def detect(slug: str) -> dict:
            if slug not in found:
                found[slug] = dl.detect_feature(repo, slug, notes)
            return found[slug]

        claims_file = state_dir / "board-claims.json"

        def refresh(first: bool) -> tuple[dict, dict, list[str]]:
            """D13: each pass reads git, the specs and the worktrees again. D10: claims. A claim goes when its
            feature has a worktree here, ships, or has no spec."""
            dl.cache_git()
            found.clear()
            pass_specs = dict(dl.specs(notes)) if not first else specs
            pass_trees = worktrees(dl, repo) if not first else trees
            tree_slugs = [s for s in dict.fromkeys(pass_trees.values()) if s]
            old_claims = read_slugs(claims_file)
            pass_claims = [c for c in dict.fromkeys([*old_claims, claim] if first and claim else old_claims)
                           if c not in tree_slugs and c in pass_specs and detect(c)["stage"] != "shipped"]
            if pass_claims != old_claims:
                claims_file.write_text(json.dumps(pass_claims) + "\n")
            return pass_specs, pass_trees, pass_claims

        specs, trees, claims = refresh(True)
        folder = notes_real / "dev-loop-board"
        if folder.is_symlink():
            payload["skipped"].append(skip("link"))
            state.save_last_run(state_dir, now, payload["skipped"])
            return 0, payload
        folder.mkdir(exist_ok=True)
        if (folder / FEATURES).is_symlink():
            payload["skipped"].append(skip("link"))
            state.save_last_run(state_dir, now, payload["skipped"])
            return 0, payload
        (folder / FEATURES).mkdir(exist_ok=True)
        events = [*drain(queue), *([(event, hook_cwd.resolve(), hook_session)] if event and hook_cwd else [])]
        claude = Path(os.environ.get("CLAUDE_CONFIG_DIR") or home / ".claude")
        ctx = Context(dl, repo, notes, folder, state_dir, store.load_machine(home), store.project_name(repo),
                      state.load(state_dir), claude)
        while True:
            write(ctx, specs, trees, claims, detect, payload, events)
            events = drain(queue)  # D2: events queued during this write.
            if not events:
                break
            specs, trees, claims = refresh(False)
        ctx.data["last_run"] = {"at": now, "skipped": payload["skipped"]}
        state.save(state_dir, ctx.data)
        payload["notes"] = list(dict.fromkeys(payload["notes"]))
        board_note(Path(top), folder, payload)
    return 0, payload


def merged_head(ctx: Context, fields: dict, found: dict) -> bool:
    """A1: a Track S note whose branch is gone (Stage 1) and whose `head` has no + line in `git cherry origin/main`."""
    head = fields.get("head")
    if found["stage"] != 1 or not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40,64}", head):
        return False
    lines = ctx.dl.cherry(ctx.repo, head)
    return lines is not None and not any(line.startswith("+") for line in lines)


def take(lock, wait: float) -> bool:
    """Flock `lock`, retrying for up to `wait` seconds."""
    end = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if time.monotonic() >= end:
                return False
            time.sleep(0.1)


def drain(queue: Path) -> list[tuple[str | None, Path, str | None]]:
    """The queued events in order, as (event, resolved cwd, session_id); the queue file is deleted. Bad lines are
    dropped."""
    taken = queue.with_name(f".{queue.name}.{os.getpid()}.drain")
    try:
        os.replace(queue, taken)  # An append after the rename lands in a new queue file.
    except FileNotFoundError:
        return []
    text = taken.read_text()
    taken.unlink()
    events = []
    for line in text.splitlines():
        try:
            item = json.loads(line)
            session = item.get("session_id")
            events.append((None if item["event"] is None else str(item["event"]), Path(str(item["cwd"])).resolve(),
                           session if isinstance(session, str) and session else None))
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    return events


def kit_version(top: Path) -> str:
    """The `version` of `.dev-loop/MANIFEST.json`, else "0.0.0"."""
    try:
        return str(json.loads((top / ".dev-loop" / "MANIFEST.json").read_text())["version"])
    except (OSError, ValueError, KeyError, TypeError):
        return "0.0.0"


def board_text(kit: str) -> str:
    """`Dev Loop Board.md` as the kit template renders it for version `kit`."""
    text = TEMPLATE.read_text().replace("/*VERSION*/", kit, 1)
    return text.replace("/*RENDER_JS*/", TEMPLATE.with_name("render.js").read_text().rstrip("\n"), 1)


def board_note(top: Path, folder: Path, payload: dict) -> None:
    """D24: write `Dev Loop Board.md` when it is missing or its frontmatter version is older than the kit version."""
    kit = kit_version(top)
    path = folder / store.BOARD_NOTE
    if path.is_file() and not path.is_symlink():
        have = (store.read_note(path) or {}).get("version")
        if have is not None and store.version_key(have) >= store.version_key(kit):
            payload["board_note"] = str(path)
            return
    try:
        store.write_file(folder, store.BOARD_NOTE, board_text(kit))
    except store.LinkError:
        payload["skipped"].append(skip("link"))
        return
    payload["board_note"] = str(path)


def write(ctx: Context, specs: dict[str, Path], trees: dict[Path, str | None], claims: list[str], detect,
          payload: dict, events: list[tuple[str | None, Path, str | None]]) -> None:
    stamp = time.time()
    now = time.strftime(TIME, time.gmtime(stamp))
    board, machine, project = ctx.folder, ctx.machine, ctx.project
    folder = board / FEATURES
    tree_slugs = [s for s in dict.fromkeys(trees.values()) if s]

    def scope(cwd: Path) -> tuple[str | None, list[str]]:
        """PB-D16: the feature of the deepest worktree holding the cwd, and the features an event there scopes: that
        feature, else the claims for the main worktree."""
        holders = [root for root in trees if inside(cwd, root)]
        if not holders:
            return None, []
        root = max(holders, key=lambda r: len(str(r)))
        slug = trees[root]
        return slug, [slug] if slug else claims if root == next(iter(trees)) else []

    scoped: list[tuple[str, list[str]]] = []
    for event, cwd, session in events:
        if event is None:  # D13: a refresh, no scope and no session mapping.
            continue
        # D12: an event in a feature worktree maps its session to that feature (the latest such event wins, so
        # PB-O22 holds for one session in two worktrees); a mapped event elsewhere scopes the mapped feature.
        tree, features = scope(cwd)
        if mapped := tree or state.feature_of(ctx.data, session):
            state.map_session(ctx.data, session, mapped, now)
        scoped.append((event, [mapped] if mapped else features))

    # D9: the write set. `kept` holds every feature whose own note must stay: written or skipped.
    kept: set[str] = set()
    for slug in dict.fromkeys([*tree_slugs, *claims]):
        result = detect(slug)
        stage = result["stage"]
        if stage == "rebase":
            payload["skipped"].append(skip("rebase", slug))
            kept.add(slug)
            continue
        if stage == "shipped" or stage == 0:
            continue
        name = store.note_name(machine["id"], project, slug)
        path = folder / name
        kept.add(slug)
        if path.is_symlink():
            payload["skipped"].append(skip("link", slug))
            continue
        if not path.exists() and (folder / f".{name}.icloud").exists():
            payload["skipped"].append(skip("placeholder", slug))
            continue
        legacy = board / name  # D3, D4: a note in `dev-loop-board/` moves into `features/`.
        if legacy.is_file() and not legacy.is_symlink():
            mine = store.read_note(legacy)
            if mine and mine.get("machine_id") == machine["id"] and not mine.get("shipped_at"):
                if path.exists():
                    legacy.unlink()
                else:
                    os.replace(legacy, path)
        # D3: an own name that parses with another machine_id stays unchanged; one that does not parse is written
        # again from the state.
        owner = store.ownership(path, machine["id"]) if path.exists() else None
        if owner == "foreign":
            payload["skipped"].append(skip("foreign", slug))
            continue
        old = store.read_note(path) if owner == "own" else None
        new_entry = slug not in ctx.data["features"]
        entry = state.entry(ctx.data, slug)
        seeded = new_entry and old is not None
        if new_entry and old is not None:
            state.seed(entry, old)  # D2: the first run of the new kit copies the old note's values.
        elif new_entry:
            entry["backfilled"] = stage >= 5  # D8
        if entry.get("shipped_at") or (old and old.get("shipped_at")):
            continue  # D12: a shipped note is never rewritten.
        waiting = str(entry.get("waiting") or "")
        if slug in claims and stage == 3 and result["worktree"] is None:
            waiting = "start Session B"
        else:
            if waiting == "start Session B":  # D6: its condition no longer holds.
                waiting = ""
            for event, features in scoped:
                if slug in features and event == "stop":
                    waiting = STOP_WAITING.get(stage, waiting)
                elif slug in features and event in ("prompt", "session-start", "post-tool"):
                    waiting = ""
        fields = merge(ctx, entry, slug, result, specs.get(slug), now, waiting)
        before = {k: v for k, v in entry.items() if k != "updated"}
        remember(entry, fields)
        # D1: `updated` moves only when a board value changes (the entry, with the digest of the other note fields),
        # never on a repair of the file (O33, a hand edit).
        changed = (new_entry and not seeded) or before != {k: v for k, v in entry.items() if k != "updated"}
        if changed or not entry.get("updated"):
            entry["updated"] = now
        fields["updated"] = entry["updated"]
        # A2: write when the file does not parse or differs from the computed fields other than `updated`.
        if old is not None and {k: v for k, v in fields.items() if k != "updated"} == {
                k: v for k, v in old.items() if k != "updated"}:
            payload["notes"].append(str(path))
            continue
        try:
            store.write_file(folder, name, store.note_text(fields))
        except store.LinkError:
            payload["skipped"].append(skip("link", slug))
            continue
        payload["notes"].append(str(path))

    # D11: the features file scopes deletion to this repo, so a run never deletes the note of another clone.
    features_file = ctx.state / "board-features.json"
    old_features = read_slugs(features_file)
    features = [f for f in old_features if f not in kept] + sorted(kept)
    own_names: set[str] = set()
    own_features: set[str] = set()
    shipped: list[tuple[str, Path]] = []
    for path in [*sorted(folder.glob("*.md")), *sorted(board.glob("*.md"))]:
        if path.name.startswith(".") or path.name == store.BOARD_NOTE or path.is_symlink():
            continue
        fields = store.read_note(path)
        feature = fields.get("feature") if fields else None
        if not fields or fields.get("machine_id") != machine["id"] or fields.get("project") != project:
            continue
        if not isinstance(feature, str) or path.name != store.note_name(machine["id"], project, feature):
            continue  # A sync conflict copy is left alone.
        if path.name in own_names:  # D4: an own note in both folders: the `dev-loop-board/` copy goes.
            path.unlink()
            payload["removed"].append(str(path))
            continue
        own_names.add(path.name)
        entry = ctx.data["features"].get(feature)
        saved = entry if isinstance(entry, dict) and entry.get("head") else fields  # D1: `head` from the state.
        if not fields.get("shipped_at") and feature not in kept and (
                detect(feature)["stage"] == "shipped"
                or feature in old_features and merged_head(ctx, saved, detect(feature))):
            # D12: the first run that sees the ship sets shipped_at, once.
            fields.update(shipped_at=now, updated=now)
            if isinstance(entry, dict):
                entry.update(shipped_at=now, updated=now)
            try:
                store.write_file(path.parent, path.name, store.note_text(fields))
            except store.LinkError:
                payload["skipped"].append(skip("link", feature))
                continue
            payload["notes"].append(str(path))
        if fields.get("shipped_at"):
            shipped.append((str(fields["shipped_at"]), path))
            continue
        if feature in kept or feature not in old_features:
            own_features.add(feature)
            continue
        path.unlink()
        payload["removed"].append(str(path))
    # D12: keep the own shipped notes of the 3 latest shipped_at in this project.
    for _, path in sorted(shipped, reverse=True)[3:]:
        path.unlink()
        payload["removed"].append(str(path))
    features = [f for f in features if f in kept or f in own_features]
    if features != old_features:
        features_file.write_text(json.dumps(features) + "\n")
    # D1: an entry goes once its feature is out of the write set and has no own-name note in either folder.
    for slug in list(ctx.data["features"]):
        name = store.note_name(machine["id"], project, slug)
        if slug not in kept and not (folder / name).exists() and not (board / name).exists():
            del ctx.data["features"][slug]

    names = own_names | {p.name for p in map(Path, payload["notes"])}
    store.clean_temps(folder, names, stamp)
    store.clean_temps(board, names, stamp)
