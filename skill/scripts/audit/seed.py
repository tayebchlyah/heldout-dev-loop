"""`audit-run seed`: the run metadata, the ledger carried from the prior run, and the trusted state, from `plan.json`."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from audit import common
from devloop import git
from seal import ACCEPTANCE_DIR

SHARED_FILES = ("run-metadata.json", "architecture.md", "coverage-ledger.json", "findings.json", "REPORT.md",
                "FINDINGS-DETAIL.md", "NEEDS-VALIDATION.md")
KEPT = ("coverage_id", "canonical_refs", "surface", "boundary", "subsystem", "attack_class", "lifecycle", "starting_paths",
        "ordinary_attack_class_block", "selected_companion_blocks", "excluded_blocks", "resolved_prior_leads",
        "resolved_prior_lead_reasons")


def prior_run(run: Path) -> tuple[Path, dict] | None:
    number = int(run.name.split()[1])
    found = []
    for path in run.parent.iterdir():
        match = re.fullmatch(r"Run (\d+)", path.name)
        if match and int(match.group(1)) < number and path.is_dir():
            found.append((int(match.group(1)), path))
    for _, path in sorted(found, reverse=True):
        try:
            metadata = common.read_json(path / common.METADATA)
        except (OSError, ValueError):
            continue
        if isinstance(metadata, dict) and metadata.get("run_status") in ("complete", "incomplete"):
            return path, metadata
    return None


def read_prior(path: Path, metadata: dict) -> tuple[list[dict], list[dict]]:
    try:
        units = common.read_json(path / common.LEDGER)
    except (OSError, ValueError):
        common.fail("prior-invalid", file=str(path / common.LEDGER))
    findings_path = path / common.FINDINGS
    if not findings_path.exists() and metadata.get("run_status") == "incomplete":
        return units, []
    try:
        findings = common.read_json(findings_path)
    except (OSError, ValueError):
        common.fail("prior-invalid", file=str(findings_path))
    if not isinstance(units, list) or not isinstance(findings, list):
        common.fail("prior-invalid", file=str(path))
    return units, findings


def prior_status(unit: dict, records: dict, changed: set[str] | None) -> str | None:
    linked = [records.get(fp) for fp in unit.get("result_fingerprints") or []]
    confirmed = [r for r in linked if r and r.get("verdict") == "confirmed"]
    if confirmed:
        traces = {t.get("file") for r in confirmed for t in r.get("trace") or [] if isinstance(t, dict)}
        same = changed is not None and not traces & changed
        return "prior_confirmed_same_source" if same else "prior_confirmed_changed_source"
    if any(r and r.get("verdict") == "needs_validation" for r in linked):
        return "prior_needs_validation"
    if any(r is None for r in linked):
        return "prior_deferred"
    if unit.get("status") in ("deferred", "blocked", "out_of_scope"):
        return f"prior_{unit['status']}"
    if unit.get("status") in ("planned", "in_progress"):
        return "prior_deferred"
    return None


def carried(unit: dict, status: str, scope: dict) -> dict:
    new = {key: unit[key] for key in KEPT if key in unit}
    new.update(prior_status=status, prior_fingerprints=sorted(unit.get("result_fingerprints") or []), attempts=[],
               wave=1, status="planned")
    common.blank_evidence(new)
    if not common.in_scope(scope, new.get("starting_paths") or []):
        new.update(status="out_of_scope", unresolved=["outside the run scope"])
    return new


def cmd_seed(args: argparse.Namespace) -> int:
    def body(run: Path, _state: dict | None) -> dict:
        if (run / common.METADATA).exists():
            common.fail("already-seeded", run=str(run))
        try:
            plan = common.read_json(run / common.PLAN)
        except (OSError, ValueError):
            common.fail("no-plan", run=str(run))
        repo = Path(args.repo).resolve()
        top = Path(git(repo, "rev-parse", "--show-toplevel") or repo)
        skill = Path(plan["skill"])
        head = plan["head"]
        exclude = list(plan.get("exclude") or [])
        scope: dict = {"kind": "whole", "spec": plan["scope"], "paths": None, "exclude": exclude}
        if plan["scope"] != "whole":
            base = plan["scope"].split("..", 1)[0]
            diff = common.git_paths(repo, "diff", "--no-renames", "--name-only", base, head)
            if diff is None:
                common.fail("git-failed", command=f"git diff --name-only {plan['scope']}")
            gone = set(exclude)
            scope.update(kind="diff", paths=sorted(p for p in diff if p not in gone and not (
                p == ACCEPTANCE_DIR or p.startswith(ACCEPTANCE_DIR + "/"))))
        prior = prior_run(run)
        units: list[dict] = []
        touched: list[dict] = []
        records: dict = {}
        gaps: list[dict] = []
        if prior is not None:
            path, metadata = prior
            prior_units, findings = read_prior(path, metadata)
            records = {r["fingerprint"]: r for r in findings if isinstance(r, dict) and "fingerprint" in r}
            commit = (metadata.get("source_ref") or {}).get("commit")
            sha = isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit)
            diff = common.git_paths(repo, "diff", "--no-renames", "--name-only", commit, head) if sha else None
            changed = set(diff) if diff is not None else None
            for unit in prior_units:
                status = prior_status(unit, records, changed)
                if status is not None:
                    units.append(carried(unit, status, scope))
                if unit.get("status") != "covered":
                    gaps.append({"coverage_id": unit.get("coverage_id"), "status": unit.get("status")})
                elif paths := sorted(p for p in unit.get("reviewed_paths") or [] if changed is None or p in changed):
                    touched.append({"coverage_id": unit["coverage_id"], "paths": paths})
        common.write_ledger(run, skill, units)
        common.write_json_atomic(run / common.METADATA, {
            "run_id": run.name,
            "repo": top.name,
            "target": str(top),
            "source_ref": {"commit": head, "dirty": False},
            "profile": plan["profile"],
            "budget": plan["budget"],
            "scope_paths": {"scope": plan["scope"], "exclude": sorted(exclude + [ACCEPTANCE_DIR + "/"])},
            "execution_policy": "sandboxed-source-and-local-only",
            "selected_companion_files": common.companion_files(units),
            "prior_run_paths": plan.get("prior_runs") or [],
            "shared_file_owners": {name: "parent" for name in SHARED_FILES},
            "result_transport": "file: agents/<agent-id>/scratch/result.json",
            "run_status": "in_progress",
        })
        common.save_state(run, {
            "run": run.name, "repo": str(top), "skill": str(skill), "profile": plan["profile"], "head": head,
            "scope": scope, "prior": prior[0].name if prior else None, "prior_records": records, "prior_gaps": gaps,
            "briefs": {}, "candidates": {}, "renamed": {}, "final": {}, "rechecks": {}, "critics": [], "folds": 0,
        })
        return {"run": run.name, "prior": prior[0].name if prior else None,
                "carried": sorted(u["coverage_id"] for u in units),
                "touched": sorted(touched, key=lambda t: t["coverage_id"]), "units": len(units)}

    return common.run_step(args, body, seeded=False)
