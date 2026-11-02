"""The `fold` rules of a critic, a verifier and a recheck result."""

from __future__ import annotations

from audit import common
from audit.brief import carried_units
from audit.fold import join_checks, new_units

ARCHIVED = ("wave", "status", "agent_id", "reviewed_paths", "local_checks", "result_fingerprints", "unresolved")


def fold_critic(state: dict, ledger: list[dict], agent: str, result: dict) -> dict:
    """Add the missing units, reassign the named units, and mark the resolved prior leads."""
    notes = result.get("notes") or {}
    quick = state["profile"] == "quick"
    new, added, duplicates = new_units(ledger, state, result["missing_units"])
    by_id = {u["coverage_id"]: u for u in ledger}
    for uid in result["reassign_ids"]:
        unit = by_id[uid]
        unit["attempts"] = list(unit.get("attempts") or []) + [
            {**{key: unit[key] for key in ARCHIVED}, "reassignment_reason": notes[uid]}]
        unit["wave"] += 1
        unit["status"] = "planned"
        common.blank_evidence(unit)
        if quick:
            unit.update(status="deferred", unresolved=["quick_profile_final_critic"])
    for fp in result["resolved_prior_leads"]:
        for unit in ledger:
            if fp in (unit.get("prior_fingerprints") or []) + (unit.get("result_fingerprints") or []):
                unit["resolved_prior_leads"] = sorted(set(unit.get("resolved_prior_leads") or []) | {fp})
                unit["resolved_prior_lead_reasons"] = {**(unit.get("resolved_prior_lead_reasons") or {}), fp: notes[fp]}
    state["critics"].append({"agent": agent, "final": state["briefs"][agent]["final"],
                             "added": sum(u["status"] == "planned" for u in new),
                             "reassigned": len(result["reassign_ids"])})
    return {"agent": agent, "kind": "critic", "added": added, "duplicates": duplicates,
            "reassigned": list(result["reassign_ids"]), "resolved": list(result["resolved_prior_leads"]),
            "stop": result["stop"]}


def relink(unit: dict, old: str, new: str) -> None:
    unit["result_fingerprints"] = sorted({new if f == old else f for f in unit.get("result_fingerprints") or []})


def fold_verifier(state: dict, ledger: list[dict], agent: str, result: dict) -> dict:
    """Keep the record as the final record of its fingerprint and join the verifier checks to the linked units."""
    fp = state["briefs"][agent]["fingerprint"]
    record = result["record"]
    new = record["fingerprint"]
    state["final"][new] = record
    state["rechecks"][new] = []
    changed = None
    if new != fp:
        state["renamed"][fp] = new
        changed = {"from": fp, "to": new}
    carried = [u for u in carried_units(ledger, fp) if u["status"] == "candidate"]
    touched = []
    for unit in ledger:
        if unit["status"] == "in_progress" and unit.get("agent_id") == agent:
            unit.update(status="candidate", result_fingerprints=[new], local_checks=[])
        elif fp in (unit.get("result_fingerprints") or []) or unit in carried:
            relink(unit, fp, new)
        else:
            continue
        join_checks(unit, result["checks"])
        touched.append(unit["coverage_id"])
    return {"agent": agent, "kind": "verifier", "fingerprint": new, "verdict": record["verdict"], "units": touched,
            "fingerprint_changed": changed}


def fold_recheck(state: dict, ledger: list[dict], agent: str, result: dict) -> dict:
    """Add the recheck verdict; a `replace` record replaces the final record."""
    fp = state["briefs"][agent]["fingerprint"]
    state["rechecks"].setdefault(fp, []).append(result["decision"])
    diff: list[str] = []
    if result["decision"] == "replace":
        old, record = state["final"][fp], result["record"]
        diff = sorted(k for k in set(old) | set(record) if old.get(k) != record.get(k))
        state["final"][fp] = record
    for unit in ledger:
        if result.get("checks") and fp in (unit.get("result_fingerprints") or []):
            join_checks(unit, result["checks"])
    return {"agent": agent, "kind": "recheck", "fingerprint": fp, "decision": result["decision"], "diff": diff}


REVIEW_KINDS = {"critic": fold_critic, "verifier": fold_verifier, "recheck": fold_recheck}
