"""`audit-run fold`: fold a checked, promoted agent result into the ledger and the trusted state, or discard a brief."""

from __future__ import annotations

import argparse
import copy
from pathlib import Path
from typing import Callable

from audit import common, safefs
from audit.brief import carried_units
from audit.check import check_result, known_agent, local_artifacts
from audit.promote import FILE_LIMIT
from audit.units import build_unit, merge_new


def new_units(ledger: list[dict], state: dict, entries: list[dict]) -> tuple[list[dict], list[str], list[str]]:
    """Build the units of `uncovered` or `missing_units` entries and merge them into the ledger."""
    built = [build_unit(entry, state) for entry in entries]
    added, duplicates = merge_new(ledger, built)
    return [u for u in built if u["coverage_id"] in added], added, duplicates


def join_checks(unit: dict, checks: list[dict]) -> None:
    """Add checks to the unit's `local_checks`; `reviewed_paths` becomes the union of all its check paths."""
    unit["local_checks"] = list(unit.get("local_checks") or []) + list(checks)
    unit["reviewed_paths"] = sorted({p for c in unit["local_checks"] for p in c["reviewed_paths"]})


def fold_hunter(state: dict, ledger: list[dict], agent: str, result: dict) -> dict:
    by_id = {u["coverage_id"]: u for u in ledger}
    for item in result["units"]:
        unit = by_id[item["coverage_id"]]
        unit.update(status=item["disposition"], local_checks=list(item["checks"]),
                    reviewed_paths=sorted(item["reviewed_paths"]),
                    result_fingerprints=sorted(set(item.get("candidate_fingerprints") or [])),
                    unresolved=list(item.get("unresolved") or []), hardening=list(result.get("hardening") or []))
    fresh = []
    for candidate in result.get("candidates") or []:
        fp = candidate["fingerprint"]
        known = state["candidates"].get(fp)
        if known is None:
            state["candidates"][fp] = {"body": candidate, "hunter": agent, "later": []}
            fresh.append(fp)
        elif agent != known["hunter"] and agent not in known["later"]:
            known["later"].append(agent)
    _, added, duplicates = new_units(ledger, state, result.get("uncovered") or [])
    return {"agent": agent, "kind": "hunter", "units": [u["coverage_id"] for u in result["units"]], "candidates": fresh,
            "added": added, "duplicates": duplicates}


KINDS: dict[str, Callable[[dict, list[dict], str, dict], dict]] = {"hunter": fold_hunter}


def discard(state: dict, ledger: list[dict], agent: str, reason: str) -> dict:
    brief = state["briefs"][agent]
    brief.update(state="discarded", reason=reason)
    held = [u for u in ledger if u["coverage_id"] in brief["units"]] if brief["kind"] == "hunter" else (
        carried_units(ledger, brief["fingerprint"]) if brief["kind"] == "verifier" else [])
    back = []
    for unit in held:
        if unit["status"] == "in_progress" and unit.get("agent_id") == agent:
            unit["status"] = "planned"
            common.blank_evidence(unit)
            back.append(unit["coverage_id"])
    return {"agent": agent, "discarded": reason, "units": back}


def cmd_fold(args: argparse.Namespace) -> int:
    def body(run: Path, saved: dict | None) -> dict:
        assert saved is not None
        known_agent(saved, args.agent)
        if saved["briefs"][args.agent]["state"] != "open":
            common.fail("already-folded", agent=args.agent, state=saved["briefs"][args.agent]["state"])
        state, ledger = copy.deepcopy(saved), common.read_ledger(run)
        if args.discard is not None:
            payload = discard(state, ledger, args.agent, args.discard)
        else:
            found, errors, result = check_result(run, saved, args.agent)
            if found != "ready" or result is None:
                common.fail("invalid", state=found, errors=errors)
            own = f"agents/{args.agent}/artifacts/"
            if missing := [p for p in local_artifacts(result)
                           if not (p.startswith(own) and safefs.read_file(run / "agents" / args.agent / "artifacts",
                                                                         p[len(own):], FILE_LIMIT) is not None)]:
                common.fail("unpromoted", paths=missing)
            kinds = dict(KINDS)
            if state["briefs"][args.agent]["kind"] != "hunter":
                from audit.fold_review import REVIEW_KINDS
                kinds.update(REVIEW_KINDS)
            payload = kinds[state["briefs"][args.agent]["kind"]](state, ledger, args.agent, result)
            state["folds"] += 1
            state["briefs"][args.agent].update(state="folded", order=state["folds"])
        common.write_ledger(run, Path(state["skill"]), ledger)
        common.sync_companions(run, ledger)
        common.save_state(run, state)
        return payload

    return common.run_step(args, body)
