#!/usr/bin/env python3
"""Shared fixtures of the held-out `audit-run` tests (spec: Audit Run Tools).

Seam 1 of the spec: `devloop.py release-plan` and `devloop.py audit-run <step>` (the kit source,
`skill/scripts/devloop.py`) as processes on a scratch git repo (`GitRepo` of `release_security_audit_support.py`
plus one commit of the files below), a scratch notes folder, and a scratch skill folder named by
`DEVLOOP_TEST_SECURITY_AUDIT_DIR`. The tests read the stdout JSON, the exit code and the files of the run folder.

Fixture choices the spec leaves open:
- The skill folder is always the fixture below, never the installed `security-audit` skill: the spec's seam 1
  names stub validators, so no case needs the real ones. It holds the headings, fences and blocks D7 and D8 name,
  each with a marker line, and a `report-schema.json` whose three branches carry a marker in `description`.
- Each stub validator appends `{target, content}` of the file it got to `<name>.log` in the skill folder and exits
  with the number in `<name>.exit` (0 when absent); `validate-findings.cjs` also fails on a file that holds
  `STUB-FAIL`.
- Parent inputs (`units` files, the report summary, files a link points at) sit outside the run folder.
- Paths in `promoted`, `rejected` and `prompt` are matched by suffix (relative or absolute both pass).
- `findings` writes `findings.json` even when it exits `open` (D16); tests read final records there.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from release_security_audit_support import GitRepo, note_text, put, record_line

SKILL_ENV = "DEVLOOP_TEST_SECURITY_AUDIT_DIR"
ACCESS = "ATTACK-CLASSES.md#Access control"
INJECTION = "ATTACK-CLASSES.md#Injection"
AI_CORE = "AI-AND-LLM.md#Core discipline"
QUICK_REASON = "quick_profile_final_critic"
SCOPE_REASON = "outside the run scope"

REPO_FILES = {name: f"def {name.replace('/', '_').replace('.py', '')}():\n    return 1\n"
              for name in ("a.py", "b.py", "c.py", "d.py", "src/auth.py", "src/upload.py", "src/export.py",
                           "src/admin.py", "src/extra.py", "src/report.py")}

CONTEXT = "CONTEXT-MARK: the sandbox paragraph, the scope, the exclude, held-out and search rules.\n"
ARCHITECTURE = "ARCH-MARK: the widget service, one HTTP entry, one upload folder.\n"

ACCESS_BLOCK = ("**Access control** (subagent_type: `general`)\n\n"
                "ACCESS-BLOCK-MARK: every object read checks that the caller owns it.\n"
                "Check the owner on the write path too.")
INJECTION_BLOCK = ("**Injection** (subagent_type: `general`)\n\n"
                   "INJECTION-BLOCK-MARK: untrusted input reaches an interpreter.")
AI_CORE_BLOCK = ("## Core discipline (include in every agent prompt for this domain)\n\n"
                 "AI-CORE-MARK: treat model output as untrusted input.")
HUNT_FENCE = "HUNT-METHOD-MARK: read each assigned unit.\n## Defensive vulnerability-finding method\nTrace each source to its sink."
PROMOTION_FENCE = "PROMOTION-MARK: trusted parent-side code promotes allowlisted files.\nNever follow a link."
RULES_FENCE = "VALIDATION-RULES-MARK: confirm a result only inside the sandbox."
VERIFIER_FENCE = "VERIFIER-PROMPT-MARK: you did not write this candidate.\nTry to refute it from source."

SKILL_FILES = {
    "SKILL.md": ("# Security Audit\n\n## Full audit setup\n\n### Write isolation\n\n"
                 "WRITE-ISOLATION-MARK: the parent is the only writer of shared run files.\n\n"
                 "## Full audit workflow\n\nWORKFLOW-MARK: six phases.\n"),
    "RECONNAISSANCE.md": ("# Reconnaissance\n\n## Deterministic coverage ledger\n\n"
                          "LEDGER-MARK: one unit per surface, boundary, subsystem and attack class.\n"),
    "HUNTING.md": ("# Vulnerability Hunting\n\n### Phase 2: Run coverage-led hunting waves\n\nPHASE2-MARK.\n\n"
                   "## Required hunter prompt\n\nPROMPT-PARTS-MARK.\n\n"
                   f"#### Core hunting method\n\n```text\n{HUNT_FENCE}\n```\n\n"
                   f"#### Promotion procedure\n\n```text\n{PROMOTION_FENCE}\n```\n\n"
                   f"#### Core validation rules\n\n```text\n{RULES_FENCE}\n```\n\n"
                   "## Structured hunter result\n\nRESULT-CONTRACT-MARK: return exactly one JSON object.\n\n"
                   "```json\n{\"units\": [], \"candidates\": [], \"hardening\": [], \"uncovered\": []}\n```\n\n"
                   "## Parent consolidation and ledger update\n\nPARENT-ONLY-MARK.\n\n"
                   "## Coverage-critic waves\n\nCRITIC-WAVES-MARK: one fresh critic after each wave.\n"),
    "VALIDATION-AND-REPORTING.md": (
        "# Validation, Structured Output, Verification, and Reporting\n\n"
        "### Phase 3: Independently validate every candidate\n\nPHASE3-MARK.\n\n"
        f"#### Candidate-verifier prompt\n\n```text\n{VERIFIER_FENCE}\n```\n\n"
        "Copy this promotion procedure verbatim into every candidate-verifier prompt:\n\n"
        f"```text\n{PROMOTION_FENCE}\n```\n\nVERIFIER-AFTER-MARK.\n\n"
        "### Phase 4: Write and validate `findings.json`\n\nPHASE4-MARK.\n\n"
        "### Phase 5: Verify the final records with fresh eyes\n\n"
        "PHASE5-MARK: one fresh verifier per final record.\n\n"
        "### Phase 6: Produce target-neutral reports from final records\n\nPHASE6-MARK.\n"),
    "ATTACK-CLASSES.md": ("# Attack Classes\n\n#### Attack classes\n\nINTRO-MARK.\n\n"
                          f"{INJECTION_BLOCK}\n\n{ACCESS_BLOCK}\n\n"
                          "**Business logic** (subagent_type: `general`)\n\nLOGIC-BLOCK-MARK.\n"),
    "AI-AND-LLM.md": ("# AI, LLM, and Agent Hunting\n\n#### When to use this file\n\nAI-WHEN-MARK.\n\n"
                      f"{AI_CORE_BLOCK}\n\n"
                      "## Context attack classes (subagent_type: `general`)\n\n"
                      "**Prompt role and provenance confusion**\n\nAI-ROLE-MARK.\n\n"
                      "## Universal moves (apply across the above)\n\nAI-MOVES-MARK.\n\n"
                      "## Validation rules (apply before reporting ANY finding here)\n\nAI-VALIDATION-MARK.\n"),
}


def schema_branch(verdict: str, mark: str) -> dict:
    return {"type": "object", "description": mark,
            "properties": {"verdict": {"type": "string", "const": verdict},
                           "fingerprint": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$"}},
            "required": ["verdict", "fingerprint"], "additionalProperties": True}


BRANCHES = {"confirmed": "SCHEMA-CONFIRMED-MARK", "needs_validation": "SCHEMA-NV-MARK",
            "rejected": "SCHEMA-REJECTED-MARK"}

STUB = r"""const fs = require("fs");
const path = require("path");
const name = path.basename(__filename, ".cjs");
const target = process.argv[2] || "";
let content = null;
try { content = fs.readFileSync(target, "utf8"); } catch (error) { content = null; }
fs.appendFileSync(path.join(__dirname, name + ".log"), JSON.stringify({ target, content }) + "\n");
let code = 0;
try { code = Number(fs.readFileSync(path.join(__dirname, name + ".exit"), "utf8").trim()) || 0; } catch (error) { code = 0; }
if (name === "validate-findings" && content !== null && content.includes("STUB-FAIL")) code = 1;
if (code !== 0) { console.log(name + ": stub rule failed"); console.error(name + ": stub rule failed"); }
process.exit(code);
"""


def write_skill(folder: Path, drop: tuple[str, ...] = ()) -> None:
    """The fixture skill; `drop` names text to cut out of a file (`FILE:text`) or a schema branch (`schema:v`)."""
    texts = dict(SKILL_FILES)
    branches = dict(BRANCHES)
    for item in drop:
        where, _, what = item.partition(":")
        if where == "schema":
            branches.pop(what)
        else:
            texts[where] = texts[where].replace(what, "")
    for name, text in texts.items():
        put(folder / name, text)
    put(folder / "report-schema.json", json.dumps(
        {"type": "array", "items": {"oneOf": [schema_branch(v, m) for v, m in branches.items()]}}, indent=2) + "\n")
    for name in ("validate-findings.cjs", "validate-coverage-ledger.cjs"):
        put(folder / name, STUB)


def enc(text: str) -> str:
    return urllib.parse.quote(text.encode("utf-8"), safe="-._~")


def cid(refs: dict) -> str:
    return "::".join(enc(refs[key]) for key in ("surface", "boundary", "subsystem", "attack_class"))


def labels(name: str, attack: str = ACCESS, subsystem: str = "src") -> dict:
    return {"surface": f"src/{name}.py#entry", "boundary": f"src/{name}.py#guard", "subsystem": subsystem,
            "attack_class": attack}


def entry(name: str, paths: list[str] | None = None, attack: str = ACCESS, companions: tuple[str, ...] = (),
          excluded: tuple[dict, ...] = (), prior: str = "none", refs: dict | None = None, **extra: object) -> dict:
    """A `units --add` entry: refs equal to the labels."""
    refs = dict(refs or labels(name, attack))
    default = f"src/{name}.py" if f"src/{name}.py" in REPO_FILES else "src/app.py"
    out = {"canonical_refs": refs, **refs, "starting_paths": list(paths or [default]),
           "ordinary_attack_class_block": refs["attack_class"] if refs["attack_class"].startswith(
               "ATTACK-CLASSES.md#") else None,
           "selected_companion_blocks": list(companions), "excluded_blocks": list(excluded), "prior_status": prior}
    out.update(extra)
    return out


def uid(name: str, attack: str = ACCESS) -> str:
    return cid(labels(name, attack))


def check_of(agent: str, paths: list[str], artifact: str | None = None, note: str = "the owner check runs") -> dict:
    return {"agent_id": agent, "reviewed_paths": list(paths), "invariant": "only the owner reads the object",
            "method": "local" if artifact else "source", "result": note,
            "artifact": f"agents/{agent}/artifacts/{artifact}" if artifact else None}


def unit_out(coverage_id: str, disposition: str, checks: list[dict], fps: tuple[str, ...] = (),
             unresolved: tuple[str, ...] = ()) -> dict:
    paths = sorted({p for c in checks for p in c["reviewed_paths"]})
    return {"coverage_id": coverage_id, "disposition": disposition, "reviewed_paths": paths, "checks": checks,
            "candidate_fingerprints": list(fps), "unresolved": list(unresolved)}


def hunter_result(units: list[dict], candidates: tuple[dict, ...] = (), hardening: tuple[str, ...] = (),
                  uncovered: tuple[dict, ...] = ()) -> dict:
    return {"units": units, "candidates": list(candidates), "hardening": list(hardening),
            "uncovered": list(uncovered)}


def trace(file: str) -> dict:
    return {"trace": [{"kind": "entrypoint", "file": file, "line": 1, "scope": "handler",
                       "description": "The request enters the handler."},
                      {"kind": "sink", "file": file, "line": 2, "scope": "handler",
                       "description": "The value reaches the sink."}],
            "evidence": [{"file": file, "line": 2, "description": "The sink call."}]}


def confirmed(fp: str, title: str, file: str = "src/auth.py", text: str = "The token is logged.") -> dict:
    return {"verdict": "confirmed", "fingerprint": fp, "title": title, "description": text,
            "root_cause": "The handler logs the whole header.", "intended_behavior": "No secret is logged.",
            **trace(file), "conditions": [],
            "execution": {"attacker_perspective": "A local reader of the log.", "payloads": ["token=dummy"],
                          "instructions": ["Run the handler test with a dummy token."],
                          "observed_result": "The dummy token is in the log."},
            "remediation": {"strategy": "Drop the header from the log line."},
            "severity": {"likelihood": {"score": "high", "reason": "Every request."},
                         "impact": {"score": "high", "reason": "Session takeover."}, "overall_severity": "high"},
            "confidence": {"score": "high", "reason": "Observed locally."}}


def needs_validation(fp: str, title: str, file: str = "src/upload.py", text: str = "The join may escape.") -> dict:
    return {"verdict": "needs_validation", "fingerprint": fp, "title": title, "description": text,
            "claimed_root_cause": "The name is joined without a containment check.", **trace(file),
            "blockers": ["The deployed media root is unknown."],
            "validation_plan": {"local": "Run the join fixture with a dotted name."}}


def rejected(fp: str, title: str, file: str = "src/admin.py") -> dict:
    return {"verdict": "rejected", "fingerprint": fp, "title": title, "description": "A cache key collision.",
            "claimed_root_cause": "The key drops the tenant.", **trace(file),
            "reason": "The tenant is part of the key one call earlier."}


def candidate(record: dict) -> dict:
    out = dict(record)
    out["proposed_verdict"] = out.pop("verdict")
    return out


def verifier_result(record: dict, agent: str, file: str) -> dict:
    return {"decision": record["verdict"], "record": record, "checks": [check_of(agent, [file])]}


def critic_result(missing: tuple[dict, ...] = (), reassign: tuple[str, ...] = (), leads: tuple[str, ...] = (),
                  stop: bool = True, notes: dict | None = None) -> dict:
    return {"missing_units": list(missing), "reassign_ids": list(reassign), "resolved_prior_leads": list(leads),
            "stop": stop, "notes": dict(notes or {})}


def ledger_unit(name: str, status: str, starting: list[str], reviewed: tuple[str, ...] = (),
                fps: tuple[str, ...] = (), unresolved: tuple[str, ...] = (), agent: str = "h01") -> dict:
    """A unit of a prior run's ledger, in the skill's shape."""
    base = entry(name, paths=starting)
    unit = {"coverage_id": cid(base["canonical_refs"]), **base, "attempts": [], "wave": 1, "status": status,
            "agent_id": None, "reviewed_paths": [], "local_checks": [], "result_fingerprints": [],
            "unresolved": list(unresolved)}
    if status in ("covered", "candidate", "blocked"):
        unit.update(agent_id=agent, reviewed_paths=list(reviewed), local_checks=[check_of(agent, list(reviewed))],
                    result_fingerprints=list(fps))
    return unit


def prior_units() -> list[dict]:
    """U1 to U6 of oracle 3: `fp-conf` is `f-c`, `fp-nv` is `f-n`, `fp-rej` is `f-r`."""
    return [ledger_unit("u1", "candidate", ["a.py"], reviewed=("a.py",), fps=("fp-conf",)),
            ledger_unit("u2", "candidate", ["b.py"], reviewed=("b.py",), fps=("fp-nv",)),
            ledger_unit("u3", "covered", ["b.py"], reviewed=("b.py",)),
            ledger_unit("u4", "covered", ["d.py"], reviewed=("d.py",)),
            ledger_unit("u5", "deferred", ["c.py"], unresolved=("budget_cannot_reserve_critics_and_validation",)),
            ledger_unit("u6", "candidate", ["d.py"], reviewed=("d.py",), fps=("fp-rej",))]


def prior_records() -> list[dict]:
    return [confirmed("fp-conf", "Token in the log", file="a.py"),
            needs_validation("fp-nv", "Join may escape", file="b.py"),
            rejected("fp-rej", "Cache key collision", file="d.py")]


def snapshot(folder: Path) -> dict[str, tuple]:
    """Every entry under `folder` (no `.git`, no stub log): relative path to its kind and bytes."""
    out: dict[str, tuple] = {}
    for base, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in dirs + files:
            path = Path(base) / name
            rel = str(path.relative_to(folder))
            if rel.endswith(".log"):
                continue
            if path.is_symlink():
                out[rel] = ("link", os.readlink(path))
            elif path.is_dir():
                out[rel] = ("dir",)
            elif path.is_file():
                out[rel] = ("file", path.read_bytes())
            else:
                out[rel] = ("other",)
    return out


class AuditRepo(GitRepo):
    """`GitRepo` (commits Z, A, B) plus commit C with `REPO_FILES`, a project note and the fixture skill."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.C = self.commit(dict(REPO_FILES), "C")
        self.skill = root / "skill"
        write_skill(self.skill)
        self.env[SKILL_ENV] = str(self.skill)
        self.inputs = root / "inputs"
        self.inputs.mkdir()
        self.n_inputs = 0
        self.write_note(note_text(None, []))

    def input(self, data: object, name: str = "input") -> Path:
        self.n_inputs += 1
        path = self.inputs / f"{name}-{self.n_inputs}.json"
        put(path, data if isinstance(data, str) else json.dumps(data, indent=2) + "\n")
        return path

    def validator_runs(self, name: str) -> list[object]:
        log = self.skill / f"{name}.log"
        runs = []
        for line in (log.read_text().splitlines() if log.exists() else []):
            content = json.loads(line).get("content")
            try:
                runs.append(json.loads(content) if content is not None else None)
            except ValueError:
                runs.append(content)
        return runs

    def validator_exit(self, name: str, code: int) -> None:
        path = self.skill / f"{name}.exit"
        if code:
            put(path, f"{code}\n")
        elif path.exists():
            path.unlink()

    def prior_run(self, n: int, commit: str, status: str, units: list[dict], records: list[dict],
                  profile: str = "standard") -> Path:
        folder = self.notes / "Security Audit" / f"Run {n}"
        put(folder / "run-metadata.json", json.dumps(
            {"run_id": f"Run {n}", "repo": "acme-widgets", "target": str(self.repo),
             "source_ref": {"commit": commit, "dirty": False}, "profile": profile, "scope_paths": [],
             "budget": None, "execution_policy": "sandboxed-source-and-local-only", "run_status": status},
            indent=2) + "\n")
        put(folder / "coverage-ledger.json", json.dumps(units, indent=2) + "\n")
        put(folder / "findings.json", json.dumps(records, indent=2) + "\n")
        return folder

    def record_at(self, run: int, sha: str) -> None:
        self.write_note(note_text([record_line(run, sha, profile="standard")], []))


class AuditRun:
    """One run folder made by `release-plan`; `call` runs `audit-run <step> --run <folder>` in the repo."""

    def __init__(self, case: unittest.TestCase, repo: AuditRepo, plan: dict) -> None:
        self.case, self.repo, self.plan = case, repo, plan
        self.path = Path(plan["output"])
        self.agent_check = False

    def call(self, step: str, *args: str) -> tuple[int, dict]:
        return self.repo.devloop("audit-run", step, "--run", str(self.path), *args)

    def ok(self, step: str, *args: str) -> dict:
        code, payload = self.call(step, *args)
        self.case.assertEqual(code, 0, f"audit-run {step} {' '.join(args)}: {payload}")
        self.case.assertNotIn("error", payload)
        return payload

    def fails(self, error: str, step: str, *args: str) -> dict:
        code, payload = self.call(step, *args)
        self.case.assertEqual(code, 1, f"audit-run {step} {' '.join(args)}: {payload}")
        self.case.assertEqual(payload.get("error"), error, payload)
        return payload

    def write_parent(self, context: bool = True, architecture: bool = True) -> None:
        if context:
            put(self.path / "context.md", CONTEXT)
        if architecture:
            put(self.path / "architecture.md", ARCHITECTURE)

    def json(self, name: str) -> object:
        return json.loads((self.path / name).read_text())

    def ledger(self) -> list[dict]:
        return self.json("coverage-ledger.json")

    def unit(self, coverage_id: str) -> dict:
        found = [u for u in self.ledger() if u.get("coverage_id") == coverage_id]
        self.case.assertEqual(len(found), 1, f"{coverage_id} in the ledger: {found}")
        return found[0]

    def metadata(self) -> dict:
        return self.json("run-metadata.json")

    def add(self, *entries: dict) -> list[str]:
        payload = self.ok("units", "--add", str(self.repo.input(list(entries), "units")))
        ids = [cid(e["canonical_refs"]) for e in entries]
        self.case.assertEqual(sorted(payload.get("added") or []), sorted(ids), payload)
        return ids

    def brief(self, kind: str, *args: str) -> str:
        payload = self.ok("brief", "--kind", kind, *args)
        agent = payload.get("agent")
        self.case.assertIsInstance(agent, str, payload)
        self.case.assertTrue((self.path / "agents" / agent / "scratch").is_dir(), payload)
        return agent

    def hunter(self, *ids: str) -> str:
        return self.brief("hunter", "--units", *ids)

    def prompt(self, agent: str) -> str:
        return (self.path / "agents" / agent / "prompt.md").read_text()

    def scratch(self, agent: str) -> Path:
        return self.path / "agents" / agent / "scratch"

    def write_result(self, agent: str, data: object) -> Path:
        path = self.scratch(agent) / "result.json"
        if path.is_symlink() or path.exists():
            path.unlink()
        put(path, data if isinstance(data, (str, bytes)) else json.dumps(data, indent=2) + "\n")
        return path

    def finish(self, agent: str, data: object, promote: bool = False) -> dict:
        """Write the result, run `check` as the agent does when `agent_check`, promote when asked, fold."""
        self.write_result(agent, data)
        if self.agent_check:
            self.case.assertEqual(self.ok("check", "--agent", agent).get("ok"), True)
        if promote:
            self.ok("promote", "--agent", agent)
        return self.ok("fold", "--agent", agent)

    def status(self) -> dict:
        return self.ok("status")

    def brief_state(self, agent: str) -> dict:
        found = [b for b in self.status().get("briefs") or [] if b.get("agent") == agent]
        self.case.assertEqual(len(found), 1, f"{agent} in status: {found}")
        return found[0]


class AuditCase(unittest.TestCase):

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.count = 0

    def repo(self) -> AuditRepo:
        self.count += 1
        return AuditRepo(self.root / f"r{self.count}")

    def plan(self, r: AuditRepo, profile: str = "standard") -> AuditRun:
        code, payload = r.devloop("release-plan", "--profile", profile)
        self.assertEqual(code, 0, payload)
        payload.pop("_stderr", None)
        return AuditRun(self, r, payload)

    def start(self, profile: str = "standard", r: AuditRepo | None = None, parent: bool = True) -> AuditRun:
        run = self.plan(r or self.repo(), profile)
        run.ok("seed")
        if parent:
            run.write_parent()
        return run

    def prior_repo(self, changed: tuple[str, ...], status: str = "complete", scoped: bool = False) -> AuditRepo:
        """Prior Run 1 at C with the units U1 to U6 of oracle 3, Run 2 with no metadata, then a commit D that
        changes `changed`; `scoped`: a record line at C, so the plan scope is `C..D`."""
        r = self.repo()
        r.prior_run(1, r.C, status, prior_units(), prior_records())
        put(r.notes / "Security Audit" / "Run 2" / "architecture.md", "An unfinished run.\n")
        if scoped:
            r.record_at(1, r.C)
        r.commit({name: f"def changed_{i}():\n    return 2\n" for i, name in enumerate(changed)}, "D")
        return r

    def hunted(self) -> tuple:
        """A run with units app, upload (brief h01) and admin (planned); the valid h01 result."""
        run = self.start()
        app, upload, admin = run.add(entry("app"), entry("upload"), entry("admin"))
        h01 = run.hunter(app, upload)
        put(run.scratch(h01) / "out" / "r.txt", "0123456789")
        result = hunter_result(
            [unit_out(app, "covered", [check_of(h01, ["src/app.py"])]),
             unit_out(upload, "candidate", [check_of(h01, ["src/upload.py"], artifact="out/r.txt")], fps=("fp-one",),
                      unresolved=("The deployed media root is unknown.",))],
            candidates=(candidate(needs_validation("fp-one", "Join may escape")),), hardening=("Pin the image.",))
        return run, h01, result, (app, upload, admin)

    def assert_same(self, before: dict, folder: Path) -> None:
        after = snapshot(folder)
        changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        self.assertEqual(changed, [], f"files written or changed under {folder}")

    def assert_errors_name(self, payload: dict, *tokens: str) -> None:
        text = json.dumps(payload.get("errors"))
        self.assertTrue(payload.get("errors"), payload)
        self.assertTrue(any(t in text for t in tokens), f"no error names one of {tokens}: {payload}")


TITLE_NV = "Upload path join\nmay leave the media root"
TITLE_CONF = "Session token\nwritten to the debug log"
HARDENING = ("Pin the base image.", "Add a body size limit.")


def full_run(case: AuditCase, skip: frozenset[str] = frozenset(), run: AuditRun | None = None) -> AuditRun:
    """A standard run up to `findings`: hunters h01 (app covered with a local artifact, upload fp-a, admin fp-b,
    auth fp-c) and h02 (export covered), critic c01 and final-clean c02, verifiers (fp-c confirmed, fp-b rejected,
    fp-a needs_validation), rechecks of fp-c and fp-a. `skip`: `final` (no c02), `verify-b` (no fp-b verifier),
    `replace-a` (the fp-a recheck is a `replace`)."""
    run = run or case.start()
    ids = run.add(entry("app"), entry("upload", paths=["src/upload.py", "src/extra.py"]), entry("admin"),
                  entry("auth", paths=["src/auth.py", "src/extra.py"]), entry("export"))
    app, upload, admin, auth, export = ids
    h01 = run.hunter(app, upload, admin, auth)
    put(run.scratch(h01) / "out" / "r.txt", "0123456789")
    nv, conf = needs_validation("fp-a", TITLE_NV), confirmed("fp-c", TITLE_CONF)
    wrong = confirmed("fp-b", "Colour cache key collision", file="src/admin.py")
    result = hunter_result(
        [unit_out(app, "covered", [check_of(h01, ["src/app.py", "src/extra.py"]),
                                   check_of(h01, ["src/app.py"], artifact="out/r.txt")]),
         unit_out(upload, "candidate", [check_of(h01, ["src/upload.py"])], fps=("fp-a",)),
         unit_out(admin, "candidate", [check_of(h01, ["src/admin.py"])], fps=("fp-b",)),
         unit_out(auth, "candidate", [check_of(h01, ["src/auth.py"])], fps=("fp-c",))],
        candidates=(candidate(conf), candidate(wrong), candidate(nv)), hardening=HARDENING)
    run.finish(h01, result, promote=True)
    h02 = run.hunter(export)
    run.finish(h02, hunter_result([unit_out(export, "covered", [check_of(h02, ["src/export.py"])])],
                                  hardening=(HARDENING[0],)), promote=True)
    run.finish(run.brief("critic"), critic_result())
    if "final" not in skip:
        run.finish(run.brief("critic", "--final"), critic_result())
    for fp, record, file in (("fp-c", conf, "src/auth.py"), ("fp-b", rejected("fp-b", wrong["title"]), "src/admin.py"),
                             ("fp-a", nv, "src/upload.py")):
        if fp == "fp-b" and "verify-b" in skip:
            continue
        agent = run.brief("verifier", "--fingerprint", fp)
        run.finish(agent, verifier_result(record, agent, file), promote=True)
    run.finish(run.brief("recheck", "--fingerprint", "fp-c"), {"decision": "verified", "fingerprint": "fp-c"})
    agent = run.brief("recheck", "--fingerprint", "fp-a")
    if "replace-a" in skip:
        changed = dict(nv, title="Upload path join may leave the media folder")
        run.finish(agent, {"decision": "replace", "reason": "A clearer title.", "record": changed})
    else:
        run.finish(agent, {"decision": "verified", "fingerprint": "fp-a"})
    return run
