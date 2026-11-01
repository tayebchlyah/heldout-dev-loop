"""Fixtures of the `audit-run` tests: a stub `security-audit` skill folder with marker texts and stub validators."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "skill" / "scripts"))
from audit import common  # noqa: E402

DEVLOOP = Path(__file__).resolve().parents[3] / "skill" / "scripts" / "devloop.py"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
EVIDENCE = ("covered", "candidate", "blocked")
FENCE = "```"

HUNTING = f"""# Hunting

## Required hunter prompt

Intro text.

#### Core hunting method - include in every hunter prompt

{FENCE}text
CORE-HUNTING-TEXT
## Inner fenced heading
INNER-AFTER-TEXT
{FENCE}

#### Promotion procedure - copy into every hunter prompt

{FENCE}text
PROMOTION-TEXT
{FENCE}

#### Core validation rules - include in every hunter prompt

{FENCE}text
VALIDATION-RULES-TEXT
{FENCE}

## Structured hunter result

STRUCTURED-RESULT-TEXT

## Parent consolidation and ledger update

CONSOLIDATION-TEXT

## Coverage-critic waves

CRITIC-WAVES-TEXT
"""

VALIDATION = f"""# Validation and reporting

### Phase 3: validate

#### Candidate-verifier prompt

{FENCE}text
VERIFIER-PROMPT-TEXT
{FENCE}

Copy this promotion procedure verbatim:

{FENCE}text
VERIFIER-PROMOTION-TEXT
{FENCE}

### Phase 4: findings

PHASE-4-TEXT

### Phase 5: Verify the final records

PHASE-5-TEXT

### Phase 6: report

PHASE-6-TEXT
"""

ATTACK = """# Attack Classes

**Access control** (subagent_type: general)

ACCESS-CONTROL-TEXT

**Injection**

INJECTION-TEXT
"""

AI = """# AI and LLM

## Core discipline (include in every agent prompt)

AI-CORE-TEXT

## Universal moves

AI-MOVES-TEXT

## Validation rules

AI-VALIDATION-TEXT
"""

VALIDATOR = """const fs = require("fs");
const path = require("path");
const name = path.basename(__filename);
fs.appendFileSync(path.join(__dirname, name + ".log"), process.argv[2] + "\\n");
const exitFile = path.join(__dirname, name + ".exit");
if (fs.existsSync(exitFile)) {
  const code = parseInt(fs.readFileSync(exitFile, "utf8"), 10);
  if (code) { console.error(name + " stub failure"); process.exit(code); }
}
"""


def stub_skill(folder: Path) -> None:
    """Write the stub skill: the headings, fences and blocks the steps read, a schema, two logging validators."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "HUNTING.md").write_text(HUNTING)
    (folder / "VALIDATION-AND-REPORTING.md").write_text(VALIDATION)
    (folder / "ATTACK-CLASSES.md").write_text(ATTACK)
    (folder / "AI-AND-LLM.md").write_text(AI)
    branches = [{"description": f"BRANCH-{verdict}", "properties": {"verdict": {"const": verdict}},
                 "required": ["verdict", "fingerprint"]} for verdict in ("confirmed", "needs_validation", "rejected")]
    (folder / "report-schema.json").write_text(json.dumps({"type": "array", "items": {"oneOf": branches}}, indent=2))
    for script in ("validate-findings.cjs", "validate-coverage-ledger.cjs"):
        (folder / script).write_text(VALIDATOR)


def validator_exit(skill: Path, script: str, code: int) -> None:
    """Make the stub validator `script` exit with `code`."""
    (skill / f"{script}.exit").write_text(str(code))


def validator_calls(skill: Path, script: str) -> list[str]:
    """The target path of each call of the stub validator `script`, in order."""
    log = skill / f"{script}.log"
    return log.read_text().splitlines() if log.exists() else []


class AuditRunCase(unittest.TestCase):
    """A scratch repo with `a.py`, `b.py`, `c.py`, a notes folder, the stub skill, and `audit-run` step runners."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo, self.notes, self.skill = self.root / "repo", self.root / "notes", self.root / "skill"
        self.repo.mkdir()
        self.notes.mkdir()
        stub_skill(self.skill)
        (self.notes / "Project Note.md").write_text("## Deferred\n")
        self.env = {**os.environ, **GIT_ENV, "DEV_LOOP_NOTES": str(self.notes),
                    "DEVLOOP_TEST_SECURITY_AUDIT_DIR": str(self.skill)}
        self.git("init", "-q", "-b", "main")
        for name in ("a.py", "b.py", "c.py"):
            (self.repo / name).write_text(name)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "start")
        self.run_dir: Path | None = None

    def git(self, *args: str) -> str:
        done = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, check=True, env=self.env)
        return done.stdout.strip()

    def commit(self, path: str, text: str) -> str:
        (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / path).write_text(text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", path)
        return self.git("rev-parse", "HEAD")

    def devloop(self, *args: str) -> tuple[dict, int]:
        done = subprocess.run([sys.executable, str(DEVLOOP), *args], capture_output=True, text=True, env=self.env)
        return json.loads(done.stdout or "{}"), done.returncode

    def release_plan(self, *args: str) -> tuple[dict, int]:
        out, code = self.devloop("release-plan", "--repo", str(self.repo), *args)
        if code == 0:
            self.run_dir = Path(out["output"])
        return out, code

    def step(self, name: str, *args: str) -> tuple[dict, int]:
        return self.devloop("audit-run", name, "--run", str(self.run_dir), "--repo", str(self.repo), *args)

    def seed(self) -> dict:
        if self.run_dir is None:
            self.release_plan()
        out, code = self.step("seed")
        self.assertEqual(code, 0, out)
        return out

    def path(self, name: str) -> Path:
        assert self.run_dir is not None
        return self.run_dir / name

    def file(self, value: object, name: str = "input.json") -> str:
        path = self.root / name
        path.write_text(json.dumps(value))
        return str(path)

    def entry(self, surface: str, *, boundary: str = "B", subsystem: str = "S",
              attack: str = "ATTACK-CLASSES.md#Access control", paths: tuple[str, ...] = ("a.py",), **extra: object) -> dict:
        refs = {"surface": surface, "boundary": boundary, "subsystem": subsystem, "attack_class": attack}
        return {"canonical_refs": refs, **refs, "starting_paths": list(paths),
                "ordinary_attack_class_block": attack if attack.startswith("ATTACK-CLASSES.md#") else None,
                "selected_companion_blocks": [], "excluded_blocks": [], "prior_status": "none", **extra}

    def add_units(self, *entries: dict) -> list[str]:
        if self.run_dir is None or not self.path("run-metadata.json").exists():
            self.seed()
        out, code = self.step("units", "--add", self.file(list(entries)))
        self.assertEqual(code, 0, out)
        return out["added"]

    def write_context(self) -> None:
        self.path("context.md").write_text("CONTEXT-TEXT\n")
        self.path("architecture.md").write_text("ARCHITECTURE-TEXT\n")

    def snapshot(self) -> dict[str, bytes]:
        run = self.path(".")
        files: dict[str, bytes] = {}
        for path in sorted(run.rglob("*")):
            key = str(path.relative_to(run))
            if path.is_symlink():
                files[key] = b"link:" + os.readlink(path).encode()
            elif path.is_file():
                files[key] = path.read_bytes()
            else:
                files[key] = b"dir"
        return files

    def ledger(self) -> list[dict]:
        return json.loads(self.path("coverage-ledger.json").read_text())

    def state(self) -> dict:
        return json.loads(self.path("audit-run.json").read_text())

    def metadata(self) -> dict:
        return json.loads(self.path("run-metadata.json").read_text())

    def prior_unit(self, surface: str, status: str, paths: tuple[str, ...] = ("a.py",), fps: tuple[str, ...] = (),
                   reviewed: tuple[str, ...] = ()) -> dict:
        refs = {"surface": surface, "boundary": "B", "subsystem": "S", "attack_class": "ATTACK-CLASSES.md#Access control"}
        reviewed_list = list(reviewed or (paths if status in EVIDENCE else ()))
        checks = [{"agent_id": "h01", "reviewed_paths": reviewed_list, "invariant": "i", "method": "source",
                   "result": "r", "artifact": None}] if reviewed_list else []
        return {"coverage_id": common.coverage_id(refs), "canonical_refs": refs, **refs, "starting_paths": list(paths),
                "ordinary_attack_class_block": "ATTACK-CLASSES.md#Access control", "selected_companion_blocks": [],
                "excluded_blocks": [], "prior_status": "new", "attempts": [], "wave": 1, "status": status,
                "agent_id": "h01" if status in EVIDENCE or status == "in_progress" else None,
                "reviewed_paths": reviewed_list, "local_checks": checks, "result_fingerprints": list(fps),
                "unresolved": ["r"] if status in ("deferred", "blocked", "out_of_scope") else []}

    def prior_run(self, n: int, status: str, units: list[dict], findings: list[dict], commit: str) -> Path:
        folder = self.notes / "Security Audit" / f"Run {n}"
        folder.mkdir(parents=True)
        (folder / "run-metadata.json").write_text(json.dumps(
            {"run_status": status, "profile": "standard", "source_ref": {"commit": commit}}))
        (folder / "coverage-ledger.json").write_text(json.dumps(units))
        (folder / "findings.json").write_text(json.dumps(findings))
        return folder

    def carried_run(self, change: str = "b.py", profile: tuple[str, ...] = ()) -> dict:
        """The prior Run 1 of U1 (`f-c` confirmed), U2 (`f-n`), U3, U4 (covered), U5 (deferred), U6 (`f-r` rejected),
        an empty Run 2, a commit to `change`, then Run 3 planned and seeded; sets `self.U1` and so on."""
        start = self.git("rev-parse", "HEAD")
        units = {
            "U1": self.prior_unit("u1", "candidate", fps=("f-c",)),
            "U2": self.prior_unit("u2", "candidate", fps=("f-n",)),
            "U3": self.prior_unit("u3", "covered", paths=("b.py",)),
            "U4": self.prior_unit("u4", "covered", paths=("c.py",)),
            "U5": self.prior_unit("u5", "deferred", paths=("c.py",)),
            "U6": self.prior_unit("u6", "candidate", fps=("f-r",)),
        }
        findings = [self.record("f-c", "confirmed"), self.record("f-n", "needs_validation"), self.record("f-r", "rejected")]
        self.prior_run(1, "complete", list(units.values()), findings, start)
        (self.notes / "Security Audit" / "Run 2").mkdir()
        self.commit(change, "changed")
        self.release_plan(*profile)
        for name, unit in units.items():
            setattr(self, name, unit["coverage_id"])
        return self.seed()

    def put_candidate(self, fp: str, body: dict, hunter: str = "h01") -> None:
        state = self.state()
        state["candidates"][fp] = {"body": body, "hunter": hunter, "later": []}
        common.save_state(self.path("."), state)

    def hunter_brief(self, *paths: str) -> tuple[str, list[str]]:
        ids = self.add_units(*(self.entry(p, paths=(p,)) for p in paths))
        self.write_context()
        out, code = self.step("brief", "--kind", "hunter", "--units", *ids)
        self.assertEqual(code, 0, out)
        return out["agent"], ids

    def src_check(self, agent: str, paths: tuple[str, ...] = ("a.py",)) -> dict:
        return {"agent_id": agent, "reviewed_paths": list(paths), "invariant": "inv", "method": "source",
                "result": "holds", "artifact": None}

    def local_check(self, agent: str, rel: str, paths: tuple[str, ...] = ("a.py",), data: bytes = b"0123456789") -> dict:
        scratch = self.path(f"agents/{agent}/scratch") / rel
        scratch.parent.mkdir(parents=True, exist_ok=True)
        scratch.write_bytes(data)
        return {**self.src_check(agent, paths), "method": "local", "artifact": f"agents/{agent}/artifacts/{rel}"}

    def candidate(self, fp: str, verdict: str = "confirmed", title: str = "T") -> dict:
        return {"proposed_verdict": verdict, "fingerprint": fp, "title": title,
                "trace": [{"kind": "sink", "file": "a.py", "line": 1}]}

    def unit_result(self, uid: str, agent: str, disposition: str = "covered", checks: list | None = None,
                    fps: tuple[str, ...] = (), unresolved: tuple[str, ...] = (), paths: tuple[str, ...] = ("a.py",)) -> dict:
        checks = checks if checks is not None else [self.src_check(agent, paths)]
        reviewed = sorted({p for c in checks for p in c["reviewed_paths"]})
        return {"coverage_id": uid, "disposition": disposition, "reviewed_paths": reviewed, "checks": checks,
                "candidate_fingerprints": list(fps), "unresolved": list(unresolved)}

    def hunter_result(self, agent: str, ids: list[str], **over: object) -> dict:
        units = [self.unit_result(uid, agent, paths=(self.unit(uid)["starting_paths"][0],)) for uid in ids]
        return {"units": units, "candidates": [], "hardening": [], "uncovered": [], **over}

    def unit(self, uid: str) -> dict:
        return next(u for u in self.ledger() if u["coverage_id"] == uid)

    def write_result(self, agent: str, value: object) -> Path:
        path = self.path(f"agents/{agent}/scratch/result.json")
        path.write_bytes(value if isinstance(value, bytes) else json.dumps(value).encode())
        return path

    def folded_hunter(self, *paths: str, candidates: tuple[dict, ...] | list[dict] = (), **over: object) -> tuple[str, list[str]]:
        """Brief a hunter on new units of `paths`, write a valid result (the first unit a candidate of every given
        candidate), promote and fold it."""
        agent, ids = self.hunter_brief(*paths)
        result = self.hunter_result(agent, ids, candidates=list(candidates), **over)
        if candidates:
            result["units"][0].update(disposition="candidate",
                                      candidate_fingerprints=[c["fingerprint"] for c in candidates])
        self.write_result(agent, result)
        for name in ("promote", "fold"):
            out, code = self.step(name, "--agent", agent)
            self.assertEqual(code, 0, out)
        return agent, ids

    def check(self, agent: str) -> tuple[dict, int]:
        return self.step("check", "--agent", agent)

    def record(self, fp: str, verdict: str, trace: tuple[str, ...] = ("a.py",)) -> dict:
        return {"fingerprint": fp, "verdict": verdict, "title": f"Title {fp}", "severity": "medium",
                "trace": [{"file": f, "line": 1} for f in trace]}
