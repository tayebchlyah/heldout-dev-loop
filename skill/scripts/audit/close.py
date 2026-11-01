"""`audit-run findings` and `audit-run status`: close a run with `findings.json` and both validators, or list the state
of every brief and unit."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from audit import common
from audit.brief import VERIFIER_OWNED
from audit.check import check_result


def final_of(state: dict, fp: str) -> dict | None:
    seen = set()
    while fp in state["renamed"] and fp not in seen:
        seen.add(fp)
        fp = state["renamed"][fp]
    return state["final"].get(fp)


def open_items(state: dict, ledger: list[dict]) -> list[dict]:
    """What keeps the run from closing (D16): open briefs, planned or in-progress units, candidates with no final
    record, final records not verified last (standard, deep), and no final-clean critic."""
    items: list[dict] = [{"kind": "brief", "agent": a} for a, b in sorted(state["briefs"].items()) if b["state"] == "open"]
    items += [{"kind": "unit", "coverage_id": u["coverage_id"], "status": u["status"]} for u in ledger
              if u["status"] in ("planned", "in_progress")]
    fps = set(state["candidates"]) | {fp for u in ledger if u.get("prior_status") in VERIFIER_OWNED
                                      and u["status"] != "out_of_scope" for fp in u.get("prior_fingerprints") or []}
    items += [{"kind": "candidate", "fingerprint": fp} for fp in sorted(fps) if final_of(state, fp) is None]
    critics = state["critics"]
    if state["profile"] == "quick":
        if not critics:
            items.append({"kind": "critic", "reason": "no critic folded"})
        return items
    for fp, record in sorted(state["final"].items()):
        if record.get("verdict") in ("confirmed", "needs_validation") and (state["rechecks"].get(fp) or [None])[-1] != "verified":
            items.append({"kind": "recheck", "fingerprint": fp})
    last = critics[-1] if critics else None
    if last is None or not last["final"] or last["added"] or last["reassigned"]:
        items.append({"kind": "critic", "reason": "no final-clean critic folded last"})
    return items


def cmd_findings(args: argparse.Namespace) -> int:
    def body(run: Path, state: dict | None) -> dict:
        assert state is not None
        skill = Path(state["skill"])
        ledger = common.read_ledger(run)
        records = [state["final"][fp] for fp in sorted(state["final"])]
        common.write_json_atomic(run / common.FINDINGS, records)
        if (tail := common.run_validator(skill, "validate-findings.cjs", run / common.FINDINGS)) is not None:
            common.fail("invalid-run", tail=tail)
        if args.incomplete is not None:
            for unit in ledger:
                if unit["status"] == "planned":
                    unit.update(status="deferred", unresolved=[args.incomplete])
            try:
                common.write_ledger(run, skill, ledger)
            except common.StepError as exc:
                if exc.payload.get("error") != "ledger-invalid":
                    raise
                common.fail("invalid-run", tail=exc.payload["tail"])
        if (tail := common.run_validator(skill, "validate-coverage-ledger.cjs", run / common.LEDGER)) is not None:
            common.fail("invalid-run", tail=tail)
        items = open_items(state, ledger)
        metadata = common.read_json(run / common.METADATA)
        if args.incomplete is not None:
            metadata.update(run_status="incomplete", incomplete_reason=args.incomplete)
        elif items:
            raise common.StepError({"error": "open", "run_status": metadata["run_status"], "records": len(records),
                                    "open": items})
        else:
            metadata["run_status"] = "complete"
        common.write_json_atomic(run / common.METADATA, metadata)
        return {"run_status": metadata["run_status"], "records": len(records), "open": items}

    return common.run_step(args, body)


def cmd_status(args: argparse.Namespace) -> int:
    def body(run: Path, state: dict | None) -> dict:
        assert state is not None
        briefs = []
        for agent, brief in sorted(state["briefs"].items()):
            found, errors = brief["state"], []
            if found == "open":
                found, errors, _ = check_result(run, state, agent)
            briefs.append({"agent": agent, "kind": brief["kind"], "state": found, "errors": errors})
        return {"run_status": common.read_json(run / common.METADATA).get("run_status"), "briefs": briefs,
                "units": dict(sorted(Counter(u["status"] for u in common.read_ledger(run)).items())),
                "counts": dict(sorted(Counter(b["kind"] for b in state["briefs"].values()).items()))}

    return common.run_step(args, body)
