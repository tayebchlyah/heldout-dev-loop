"""`audit-run check`: an agent's `result.json` against the rules of its brief kind; read no-follow and bounded."""

from __future__ import annotations

import argparse
import json
import os
import secrets
from pathlib import Path

from audit import common, safefs
from audit.units import entry_errors

RESULT_LIMIT = 5 * 1024 * 1024
MAX_ERRORS = 50
VERDICTS = ("confirmed", "needs_validation", "rejected")


def visible(value: object) -> bool:
    return isinstance(value, str) and not common.ref_errors(value)


def record_errors(run: Path, skill: Path, records: list[dict], label: str) -> list[str]:
    """Run `validate-findings.cjs` on the records in a temp file of the run folder: `[]`, or one error with its tail."""
    temp = run / f".check-{secrets.token_hex(6)}.json"
    try:
        temp.write_text(json.dumps(records, ensure_ascii=False))
        tail = common.run_validator(skill, "validate-findings.cjs", temp)
    finally:
        temp.unlink(missing_ok=True)
    return [] if tail is None else [f"{label}: {tail}"]


def check_errors(prefix: str, check: object, agent: str, run: Path) -> list[str]:
    """Why one check object breaks the result rules: owner, paths, text, method and artifact."""
    if not isinstance(check, dict):
        return [f"{prefix}: expected an object"]
    errors = []
    if check.get("agent_id") != agent:
        errors.append(f"{prefix}.agent_id: expected {agent}")
    paths = check.get("reviewed_paths")
    if not isinstance(paths, list) or not paths:
        errors.append(f"{prefix}.reviewed_paths: expected a non-empty list")
    else:
        errors += [f"{prefix}.reviewed_paths: {p!r}: {why}" for p in paths if (why := common.path_error(p))]
    for field in ("invariant", "result"):
        if not visible(check.get(field)):
            errors.append(f"{prefix}.{field}: expected visible text")
    artifact = check.get("artifact")
    if check.get("method") == "source":
        if artifact is not None:
            errors.append(f"{prefix}.artifact: a source check has artifact null")
    elif check.get("method") == "local":
        own = f"agents/{agent}/artifacts/"
        rel = artifact[len(own):] if isinstance(artifact, str) and artifact.startswith(own) else None
        if rel is None or rel.endswith("/") or common.path_error(rel):
            errors.append(f"{prefix}.artifact: {artifact!r} is not agents/{agent}/artifacts/<relative path>")
        elif not scratch_file(run, agent, rel):
            errors.append(f"{prefix}.artifact: {artifact!r} has no regular file agents/{agent}/scratch/{rel}")
    else:
        errors.append(f"{prefix}.method: expected source or local")
    return errors


def scratch_file(run: Path, agent: str, rel: str) -> bool:
    *folders, leaf = rel.split("/")
    try:
        dir_fd = safefs.walk(run, ["agents", agent, "scratch", *folders])
    except OSError:
        return False
    try:
        fd = safefs.open_leaf(dir_fd, leaf)
    except OSError:
        return False
    finally:
        os.close(dir_fd)
    try:
        return safefs.regular_single(fd) is not None
    finally:
        os.close(fd)


def local_artifacts(result: dict) -> list[str]:
    """Every artifact of a `local` check of the result (unit checks, then top-level checks), unique, in order."""
    def entries(holder: object) -> list:
        value = holder.get("checks") if isinstance(holder, dict) else None
        return value if isinstance(value, list) else []

    units = result.get("units")
    checks = [c for u in (units if isinstance(units, list) else []) for c in entries(u)] + entries(result)
    found: list[str] = []
    for check in checks:
        if isinstance(check, dict) and check.get("method") == "local" and isinstance(check.get("artifact"), str):
            if check["artifact"] not in found:
                found.append(check["artifact"])
    return found


def new_entry_errors(prefix: str, entry: object) -> list[str]:
    errors = [f"{prefix}: {e}" for e in entry_errors(entry, refs_given=False)]
    if isinstance(entry, dict) and not visible(entry.get("reason")):
        errors.append(f"{prefix}.reason: expected visible text")
    return errors


def lists(result: dict, fields: tuple[str, ...], required: bool = False) -> list[str]:
    default: list | None = None if required else []
    return [f"{field}: expected a list" for field in fields if not isinstance(result.get(field, default), list)]


def hunter_errors(run: Path, state: dict, brief: dict, agent: str, result: dict) -> list[str]:
    errors = lists(result, ("units", "candidates", "hardening", "uncovered"))
    if errors:
        return errors
    candidates = result.get("candidates", [])
    fps: list[str] = []
    for index, candidate in enumerate(candidates):
        fp = candidate.get("fingerprint") if isinstance(candidate, dict) else None
        if not isinstance(fp, str) or not common.FINGERPRINT_RE.match(fp):
            errors.append(f"candidates[{index}].fingerprint: {fp!r} does not match {common.FINGERPRINT_RE.pattern}")
        elif fp in fps:
            errors.append(f"candidates[{index}].fingerprint: {fp} twice")
        if isinstance(candidate, dict) and candidate.get("proposed_verdict") not in ("confirmed", "needs_validation"):
            errors.append(f"candidates[{index}].proposed_verdict: expected confirmed or needs_validation")
        if isinstance(fp, str):
            fps.append(fp)
    known = set(fps) | set(state["candidates"])
    seen: list[str] = []
    for index, unit in enumerate(result.get("units", [])):
        if not isinstance(unit, dict):
            errors.append(f"units[{index}]: expected an object")
            continue
        uid = unit.get("coverage_id")
        prefix = f"units[{index}] {uid}"
        if uid not in brief["units"]:
            errors.append(f"{prefix}: not assigned to {agent}")
        elif uid in seen:
            errors.append(f"{prefix}: twice")
        seen.append(uid)
        errors += unit_errors(prefix, unit, agent, run, known)
    errors += [f"units: {uid} assigned to {agent} is missing" for uid in brief["units"] if uid not in seen]
    if any(not isinstance(h, str) for h in result.get("hardening", [])):
        errors.append("hardening: expected a list of strings")
    for index, entry in enumerate(result.get("uncovered", [])):
        errors += new_entry_errors(f"uncovered[{index}]", entry)
    if candidates and not errors:
        renamed = [{("verdict" if k == "proposed_verdict" else k): v for k, v in c.items()} for c in candidates]
        errors += record_errors(run, Path(state["skill"]), renamed, "candidates")
    return errors


def unit_errors(prefix: str, unit: dict, agent: str, run: Path, known: set[str]) -> list[str]:
    errors = []
    disposition = unit.get("disposition")
    checks, paths = unit.get("checks"), unit.get("reviewed_paths")
    fps, unresolved = unit.get("candidate_fingerprints", []), unit.get("unresolved", [])
    if not all(isinstance(v, list) for v in (checks, paths, fps, unresolved)):
        return [f"{prefix}: checks, reviewed_paths, candidate_fingerprints and unresolved are lists"]
    if disposition not in ("covered", "candidate", "blocked"):
        errors.append(f"{prefix}.disposition: expected covered, candidate or blocked")
    if not paths or not checks:
        errors.append(f"{prefix}: reviewed_paths and checks must not be empty")
    if disposition == "covered" and unresolved:
        errors.append(f"{prefix}.unresolved: a covered unit has none")
    if disposition == "blocked" and not unresolved:
        errors.append(f"{prefix}.unresolved: a blocked unit needs one")
    if disposition == "candidate" and not fps:
        errors.append(f"{prefix}.candidate_fingerprints: a candidate unit needs one")
    if disposition in ("covered", "blocked") and fps:
        errors.append(f"{prefix}.candidate_fingerprints: only a candidate unit has fingerprints")
    errors += [f"{prefix}.reviewed_paths: {p!r}: {why}" for p in paths if (why := common.path_error(p))]
    for n, check in enumerate(checks):
        errors += check_errors(f"{prefix}.checks[{n}]", check, agent, run)
    owned = {p for c in checks if isinstance(c, dict) and isinstance(c.get("reviewed_paths"), list)
             for p in c["reviewed_paths"] if isinstance(p, str)}
    if set(p for p in paths if isinstance(p, str)) != owned:
        errors.append(f"{prefix}.reviewed_paths: not the union of its checks' reviewed_paths")
    for fp in fps:
        if not isinstance(fp, str) or not common.FINGERPRINT_RE.match(fp):
            errors.append(f"{prefix}.candidate_fingerprints: {fp!r} does not match {common.FINGERPRINT_RE.pattern}")
        elif fp not in known:
            errors.append(f"{prefix}.candidate_fingerprints: {fp} is no candidate of this result or of the run")
    if any(not visible(u) for u in unresolved):
        errors.append(f"{prefix}.unresolved: expected visible text")
    return errors


def critic_errors(run: Path, result: dict) -> list[str]:
    errors = lists(result, ("missing_units", "reassign_ids", "resolved_prior_leads"), required=True)
    if errors:
        return errors
    ledger = {u["coverage_id"]: u for u in common.read_ledger(run)}
    leads = {fp for u in ledger.values() for fp in (u.get("prior_fingerprints") or []) + (u.get("result_fingerprints") or [])}
    notes = result.get("notes", {})
    if not isinstance(notes, dict):
        errors.append("notes: expected an object")
        notes = {}
    for index, entry in enumerate(result["missing_units"]):
        errors += new_entry_errors(f"missing_units[{index}]", entry)
    for index, uid in enumerate(result["reassign_ids"]):
        if isinstance(uid, str) and uid in result["reassign_ids"][:index]:
            errors.append(f"reassign_ids: {uid} repeated")
        if not isinstance(uid, str) or ledger.get(uid, {}).get("status") not in ("blocked", "covered", "candidate"):
            errors.append(f"reassign_ids: {uid!r} is no blocked, covered or candidate unit")
        if not visible(notes.get(uid) if isinstance(uid, str) else None):
            errors.append(f"notes: no reason for reassign id {uid!r}")
    for fp in result["resolved_prior_leads"]:
        if not isinstance(fp, str) or fp not in leads:
            errors.append(f"resolved_prior_leads: {fp!r} is linked to no unit")
        if not visible(notes.get(fp) if isinstance(fp, str) else None):
            errors.append(f"notes: no reason for resolved lead {fp!r}")
    if not isinstance(result.get("stop"), bool):
        errors.append("stop: expected true or false")
    elif result["stop"] and (result["missing_units"] or result["reassign_ids"]):
        errors.append("stop: true only with no missing_units and no reassign_ids")
    return errors


def record_rules(run: Path, state: dict, record: object, fp: str | None, label: str) -> list[str]:
    if not isinstance(record, dict):
        return [f"{label}: expected an object"]
    got = record.get("fingerprint")
    if not isinstance(got, str) or not common.FINGERPRINT_RE.match(got):
        return [f"{label}.fingerprint: {got!r} does not match {common.FINGERPRINT_RE.pattern}"]
    if fp is not None and got != fp:
        return [f"{label}.fingerprint: {got} is not {fp}"]
    return record_errors(run, Path(state["skill"]), [record], label)


def checks_errors(run: Path, agent: str, checks: object, required: bool) -> list[str]:
    if not isinstance(checks, list) or (required and not checks):
        return ["checks: expected a non-empty list" if required else "checks: expected a list"]
    return [e for n, c in enumerate(checks) for e in check_errors(f"checks[{n}]", c, agent, run)]


def verifier_errors(run: Path, state: dict, agent: str, result: dict) -> list[str]:
    decision, record = result.get("decision"), result.get("record")
    errors = []
    if decision not in VERDICTS:
        errors.append("decision: expected confirmed, needs_validation or rejected")
    if isinstance(record, dict) and record.get("verdict") != decision:
        errors.append(f"record.verdict: {record.get('verdict')!r} is not the decision {decision!r}")
    errors += checks_errors(run, agent, result.get("checks"), True)
    errors = errors or record_rules(run, state, record, None, "record")
    return errors or other_fingerprint_errors(run, state, agent, record)


def other_fingerprint_errors(run: Path, state: dict, agent: str, record: dict) -> list[str]:
    """A verifier record keeps the brief fingerprint or takes a new one; a fingerprint the run knows is refused."""
    new = record["fingerprint"]
    if new == state["briefs"][agent]["fingerprint"]:
        return []
    known = {*state["final"], *state["candidates"], *state["renamed"], *state["renamed"].values()}
    for unit in common.read_ledger(run):
        known |= set(unit.get("result_fingerprints") or []) | set(unit.get("prior_fingerprints") or [])
    return [f"record.fingerprint: {new} is another fingerprint of the run"] if new in known else []


def recheck_errors(run: Path, state: dict, brief: dict, agent: str, result: dict) -> list[str]:
    fp = brief["fingerprint"]
    errors = checks_errors(run, agent, result["checks"], False) if "checks" in result else []
    if result.get("decision") == "verified":
        if result.get("fingerprint") != fp:
            errors.append(f"fingerprint: expected {fp}")
        return errors
    if result.get("decision") != "replace":
        return errors + ["decision: expected verified or replace"]
    if not visible(result.get("reason")):
        errors.append("reason: expected visible text")
    return errors or record_rules(run, state, result.get("record"), fp, "record")


def read_result(run: Path, agent: str) -> tuple[str, list[str], dict | None]:
    path = run / "agents" / agent / "scratch" / "result.json"
    if not os.path.lexists(path):
        return "missing", [], None
    data = safefs.read_file(run, f"agents/{agent}/scratch/result.json", RESULT_LIMIT)
    if data is None:
        return "invalid", [f"agents/{agent}/scratch/result.json: a link, not a regular file, or over 5 MiB"], None
    try:
        result = json.loads(data.decode("utf-8"))
    except ValueError as exc:
        return "invalid", [f"result.json: not JSON: {type(exc).__name__}"], None
    if not isinstance(result, dict):
        return "invalid", ["result.json: expected one JSON object"], None
    return "ready", [], result


def check_result(run: Path, state: dict, agent: str) -> tuple[str, list[str], dict | None]:
    """The state of an agent's result (`missing`, `invalid` with errors, `ready`), with the result when parsed."""
    found, errors, result = read_result(run, agent)
    if result is None:
        return found, errors, None
    brief = state["briefs"][agent]
    kind = brief["kind"]
    if kind == "hunter":
        errors = hunter_errors(run, state, brief, agent, result)
    elif kind == "critic":
        errors = critic_errors(run, result)
    elif kind == "verifier":
        errors = verifier_errors(run, state, agent, result)
    else:
        errors = recheck_errors(run, state, brief, agent, result)
    return ("invalid", errors[:MAX_ERRORS], result) if errors else ("ready", [], result)


def known_agent(state: dict, agent: str) -> None:
    """Stop with `unknown-agent` when no brief has the id."""
    if agent not in state["briefs"]:
        common.fail("unknown-agent", agent=agent)


def cmd_check(args: argparse.Namespace) -> int:
    def body(run: Path, state: dict | None) -> dict:
        assert state is not None
        known_agent(state, args.agent)
        found, errors, _ = check_result(run, state, args.agent)
        if found != "ready":
            raise common.StepError({"ok": False, "state": found, "errors": errors})
        return {"ok": True}

    return common.run_step(args, body)
