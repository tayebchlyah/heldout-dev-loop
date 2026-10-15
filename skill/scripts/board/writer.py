"""`devloop.py board`: write the status notes of this machine from local state (spec D6 to D14, D18)."""

from __future__ import annotations

import fcntl
import json
import re
import sys
import time
from pathlib import Path
from types import ModuleType

from . import store

TIME = "%Y-%m-%dT%H:%M:%SZ"
STOP_WAITING = {2: "approve the spec", 3: "reply in the session", 4: "reply in the session",
                5: "reply in the session", 6: "reply in the session", 7: "review the PR"}
TEMPLATE = Path(__file__).with_name("board-note.md")


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


def tasks(plan: Path | None, ledger: Path | None, before: list, stage5: str | None, now: str) -> list[dict]:
    """D14: plan headings, done from the ledger at its first sight; the first task not done is `doing`."""
    if plan is None:
        return []
    titles: dict[int, str] = {}
    for n, title in re.findall(r"^#{2,3} Task (\d+)\b[:.]?\s*(.*)$", plan.read_text(), re.MULTILINE):
        titles.setdefault(int(n), title.strip())
    complete = set()
    if ledger is not None and ledger.is_file():
        complete = {int(n) for n in re.findall(r"^Task (\d+): complete", ledger.read_text(), re.MULTILINE)}
    old = {t.get("n"): t for t in before if isinstance(t, dict)}
    out: list[dict] = []
    previous_done, doing = None, False
    for n in sorted(titles):
        seen = old.get(n, {})
        task: dict = {"n": n, "title": titles[n], "state": "todo"}
        if n in complete or not doing:
            started = seen.get("started") or previous_done or stage5
            if started:
                task["started"] = started
            if n in complete:
                task.update(state="done", done=seen.get("done") or now)
            else:
                task["state"], doing = "doing", True
        previous_done = task.get("done")
        out.append(task)
    return out


def merge(dl: ModuleType, old: dict, slug: str, found: dict, spec: Path | None, notes: Path,
          machine: dict, project: str, now: str, waiting: str) -> dict:
    """The new status note of one feature from its detect result, its spec, plan and ledger, and its old note."""
    stage: int = found["stage"]
    times = dict(old["stage_times"]) if isinstance(old.get("stage_times"), dict) else {}
    seen_late = any(k.isdigit() and int(k) >= 3 for k in times)  # D18: a run already saw Stage 3 or later
    times.setdefault(str(stage), now)
    text = spec.read_text() if spec else ""
    title = re.search(r"^# (.+)$", text, re.MULTILINE)
    goal = re.split(r"\n\s*\n", section(text, "Goal"))[0]
    assumptions = [a.strip() for a in re.findall(r"^[-*] (.+)$", section(text, "Assumptions"), re.MULTILINE)]
    baseline = old["assumptions_baseline"] if isinstance(old.get("assumptions_baseline"), list) else []
    if stage >= 3 and not seen_late:
        baseline = assumptions
    plan = dl.find_plan(notes, spec) if spec else None
    worktree = found["worktree"]
    ledger = Path(worktree) / ".superpowers" / "sdd" / plan.stem / "progress.md" if plan and worktree else None
    before = old["tasks"] if isinstance(old.get("tasks"), list) else []
    return {
        "type": "dev-loop-status",
        "schema": 1,
        "project": project,
        "feature": slug,
        "title": title.group(1).strip() if title else slug,
        "description": " ".join(line.strip() for line in goal.splitlines()),
        "machine_id": machine["id"],
        "owner": machine["owner"],
        "machine": machine["machine"],
        "track": found["track"] or "",
        "stage": stage,
        "stage_times": times,
        "tasks": tasks(plan, ledger, before, times.get("5"), now),
        "waiting": waiting,
        "decisions": [a for a in assumptions if a not in baseline] if stage >= 3 or seen_late else [],
        "assumptions_baseline": baseline,
        "shipped_at": "",
        "updated": now,
    }


def run(dl: ModuleType, repo: Path, notes: Path, home: Path, *, event: str | None, hook_cwd: Path | None,
        claim: str | None) -> tuple[int, dict]:
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
    specs = dict(dl.specs(notes))
    claim = claim.lower() if claim else None
    if claim and claim not in specs:
        return 1, {"error": f"no spec for {claim} in {notes / 'specs'}"}

    common = Path(dl.git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    trees = worktrees(dl, repo)
    notes_real = notes.resolve()
    if any(inside(notes_real, root) for root in [*trees, common]):
        payload["skipped"].append(skip("notes-in-repo"))
        return 0, payload
    state = common / "dev-loop"
    state.mkdir(parents=True, exist_ok=True)
    with open(state / "board.lock", "a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            payload["skipped"].append(skip("busy"))
            return 0, payload
        folder = notes_real / "dev-loop-board"
        if folder.is_symlink():
            payload["skipped"].append(skip("link"))
            return 0, payload
        folder.mkdir(exist_ok=True)
        write(dl, repo, notes, home, folder, state, specs, trees, claim, payload, event,
              hook_cwd.resolve() if event and hook_cwd else None)
        board_note(Path(top), folder, payload)
    return 0, payload


def version(text: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in text.strip().strip("\"'").split("."))
    except ValueError:
        return ()


def kit_version(top: Path) -> str:
    """The `version` of `.dev-loop/MANIFEST.json`, else "0.0.0"."""
    try:
        return str(json.loads((top / ".dev-loop" / "MANIFEST.json").read_text())["version"])
    except (OSError, ValueError, KeyError, TypeError):
        return "0.0.0"


def board_note(top: Path, folder: Path, payload: dict) -> None:
    """D24: write `Dev Loop Board.md` when it is missing or its frontmatter version is older than the kit version."""
    kit = kit_version(top)
    path = folder / store.BOARD_NOTE
    if path.is_file() and not path.is_symlink():
        lines = path.read_text().splitlines()
        head = lines[1:lines.index("---", 1)] if lines[:1] == ["---"] and "---" in lines[1:] else []
        have = next((version(line.partition(":")[2]) for line in head if line.startswith("version:")), ())
        if have >= version(kit):
            payload["board_note"] = str(path)
            return
    text = TEMPLATE.read_text().replace("/*VERSION*/", kit, 1)
    text = text.replace("/*RENDER_JS*/", TEMPLATE.with_name("render.js").read_text().rstrip("\n"), 1)
    try:
        store.write_file(folder, store.BOARD_NOTE, text)
    except store.LinkError:
        payload["skipped"].append(skip("link"))
        return
    payload["board_note"] = str(path)


def write(dl: ModuleType, repo: Path, notes: Path, home: Path, folder: Path, state: Path, specs: dict[str, Path],
          trees: dict[Path, str | None], claim: str | None, payload: dict, event: str | None,
          cwd: Path | None) -> None:
    stamp = time.time()
    now = time.strftime(TIME, time.gmtime(stamp))
    machine = store.load_machine(home)
    project = store.project_name(repo)
    found: dict[str, dict] = {}

    def detect(slug: str) -> dict:
        if slug not in found:
            found[slug] = dl.detect_feature(repo, slug, notes)
        return found[slug]

    # D10: claims. A claim goes when its feature has a worktree here, ships, or has no spec.
    tree_slugs = [s for s in dict.fromkeys(trees.values()) if s]
    claims_file = state / "board-claims.json"
    old_claims = read_slugs(claims_file)
    claims = [c for c in dict.fromkeys([*old_claims, claim] if claim else old_claims)
              if c not in tree_slugs and c in specs and detect(c)["stage"] != "shipped"]
    if claims != old_claims:
        claims_file.write_text(json.dumps(claims) + "\n")

    # D16: the session scope of an event: the feature of the worktree that holds the cwd (the deepest one), or
    # the claims in the main worktree on the default branch.
    scope: list[str] = []
    holders = [root for root in trees if cwd and inside(cwd, root)]
    if holders:
        root = max(holders, key=lambda r: len(str(r)))
        slug = trees[root]
        scope = [slug] if slug else claims if root == next(iter(trees)) else []

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
        old = store.read_note(path) if path.exists() else {}
        if old is None or (old and old.get("machine_id") != machine["id"]) or (old and old.get("shipped_at")):
            continue  # D2: a file we cannot prove our own stays unchanged. D12: a shipped note is never rewritten.
        waiting = str(old.get("waiting") or "")
        if slug in claims and stage == 3 and result["worktree"] is None:
            waiting = "start Session B"
        elif slug in scope and event == "stop":
            waiting = STOP_WAITING.get(stage, waiting)
        elif slug in scope and event in ("prompt", "session-start"):
            waiting = ""
        fields = merge(dl, old, slug, result, specs.get(slug), notes, machine, project, now, waiting)
        try:
            store.write_file(folder, name, store.note_text(fields))
        except store.LinkError:
            payload["skipped"].append(skip("link", slug))
            continue
        payload["notes"].append(str(path))

    # D11: delete an own note (by frontmatter, under its own name) of a feature that left the write set of this
    # repo. The features file records that set, so a run never deletes the note of another clone of the project.
    features_file = state / "board-features.json"
    old_features = read_slugs(features_file)
    features = [f for f in old_features if f not in kept] + sorted(kept)
    own_names: set[str] = set()
    own_features: set[str] = set()
    shipped: list[tuple[str, Path]] = []
    for path in sorted(folder.glob("*.md")):
        if path.name.startswith(".") or path.name == store.BOARD_NOTE or path.is_symlink():
            continue
        fields = store.read_note(path)
        feature = fields.get("feature") if fields else None
        if not fields or fields.get("machine_id") != machine["id"] or fields.get("project") != project:
            continue
        if not isinstance(feature, str) or path.name != store.note_name(machine["id"], project, feature):
            continue  # A sync conflict copy is left alone.
        own_names.add(path.name)
        if not fields.get("shipped_at") and feature not in kept and detect(feature)["stage"] == "shipped":
            # D12: the first run that sees the ship sets shipped_at, once.
            fields.update(shipped_at=now, updated=now)
            try:
                store.write_file(folder, path.name, store.note_text(fields))
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

    store.clean_temps(folder, own_names | {p.name for p in map(Path, payload["notes"])}, stamp)
