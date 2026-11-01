"""`audit-run units`: add parent-planned units, update the paths and blocks of a planned unit, and build the units a
hunter or critic result names."""

from __future__ import annotations

import argparse
from pathlib import Path

from audit import common

LABELS = common.REF_KEYS
BLOCK_FIELDS = ("ordinary_attack_class_block", "selected_companion_blocks", "excluded_blocks")
SAME_FIELDS = ("starting_paths",) + BLOCK_FIELDS
ADD_STATUSES = ("planned", "not_applicable", "out_of_scope", "deferred")


def visible(value: object) -> bool:
    return isinstance(value, str) and not common.ref_errors(value)


def field_errors(entry: dict) -> list[str]:
    errors = []
    if "starting_paths" in entry:
        paths = entry["starting_paths"]
        if not isinstance(paths, list) or not paths:
            errors.append("starting_paths: expected a non-empty list")
        else:
            errors += [f"starting_paths: {p!r}: {why}" for p in paths if (why := common.path_error(p))]
    block = entry.get("ordinary_attack_class_block")
    if block is not None and not visible(block):
        errors.append("ordinary_attack_class_block: expected null or visible text")
    blocks = entry.get("selected_companion_blocks", [])
    if not isinstance(blocks, list) or not all(visible(b) for b in blocks):
        errors.append("selected_companion_blocks: expected a list of visible text")
    excluded = entry.get("excluded_blocks", [])
    if not isinstance(excluded, list) or not all(
            isinstance(e, dict) and visible(e.get("block")) and visible(e.get("reason")) for e in excluded):
        errors.append("excluded_blocks: expected a list of {block, reason}")
    return errors


def entry_errors(entry: object, *, refs_given: bool) -> list[str]:
    """Why a unit entry is not valid: refs (given, or the four labels), labels, starting paths, block fields, and for
    `units --add` the prior status and the parent's status and reason."""
    if not isinstance(entry, dict):
        return ["expected an object"]
    errors = []
    refs = entry.get("canonical_refs") if refs_given and "canonical_refs" in entry else {k: entry.get(k) for k in LABELS}
    if not isinstance(refs, dict) or not set(LABELS) <= set(refs) or set(refs) - set(LABELS) - {"lifecycle"}:
        errors.append("canonical_refs: expected surface, boundary, subsystem, attack_class and an optional lifecycle")
    else:
        errors += [f"canonical_refs.{key}: {why}" for key, value in refs.items() for why in common.ref_errors(value)]
    errors += [f"{key}: expected visible text" for key in LABELS if key in entry and not visible(entry[key])]
    if "starting_paths" not in entry:
        errors.append("starting_paths: missing")
    errors += field_errors(entry)
    if refs_given:
        if entry.get("prior_status", "none") not in common.PRIOR_STATUSES:
            errors.append("prior_status: not a prior status of the skill")
        status = entry.get("status", "planned")
        if status not in ADD_STATUSES:
            errors.append(f"status: expected one of {', '.join(ADD_STATUSES)}")
        elif status != "planned" and not visible(entry.get("reason")):
            errors.append("reason: a unit that is not planned needs a visible reason")
    return errors


def new_unit(refs: dict, entry: dict, prior_status: str) -> dict:
    unit = {"coverage_id": common.coverage_id(refs), "canonical_refs": refs}
    unit.update({key: entry.get(key, refs[key]) for key in LABELS})
    if "lifecycle" in refs:
        unit["lifecycle"] = refs["lifecycle"]
    unit.update(starting_paths=list(entry["starting_paths"]),
                ordinary_attack_class_block=entry.get("ordinary_attack_class_block"),
                selected_companion_blocks=list(entry.get("selected_companion_blocks") or []),
                excluded_blocks=list(entry.get("excluded_blocks") or []),
                prior_status=prior_status, attempts=[], wave=1, status="planned")
    common.blank_evidence(unit)
    return unit


def build_unit(entry: dict, state: dict) -> dict:
    """The planned unit of an `uncovered` or `missing_units` entry: the labels are the refs (profile `quick`: the quick
    subsystem), block fields from the attack class, and the scope and profile status."""
    refs = {key: entry[key] for key in LABELS}
    if state["profile"] == "quick":
        refs["subsystem"] = common.QUICK_SUBSYSTEM
    attack = entry["attack_class"]
    blocks = list(entry.get("selected_companion_blocks") or [])
    if not attack.startswith("ATTACK-CLASSES.md#") and "#" in attack and attack not in blocks:
        blocks.append(attack)
    unit = new_unit(refs, {**entry, "selected_companion_blocks": blocks,
                           "ordinary_attack_class_block": entry.get("ordinary_attack_class_block") or (
                               attack if attack.startswith("ATTACK-CLASSES.md#") else None)},
                    "new" if state.get("prior") else "none")
    if not common.in_scope(state["scope"], unit["starting_paths"]):
        reason = entry.get("reason")
        unit.update(status="out_of_scope", unresolved=([reason] if visible(reason) else []) + ["outside the run scope"])
    elif state["profile"] == "quick":
        unit.update(status="deferred", unresolved=["quick_profile_final_critic"])
    return unit


def merge_new(ledger: list[dict], new: list[dict]) -> tuple[list[str], list[str]]:
    """Append the new units to the ledger; a known id with the same paths and blocks is a duplicate, with others a
    `collision`."""
    known = {u["coverage_id"]: u for u in ledger}
    added, duplicates = [], []
    for unit in new:
        old = known.get(unit["coverage_id"])
        if old is None:
            ledger.append(unit)
            known[unit["coverage_id"]] = unit
            added.append(unit["coverage_id"])
        elif all(old.get(key) == unit.get(key) for key in SAME_FIELDS):
            duplicates.append(unit["coverage_id"])
        else:
            common.fail("collision", coverage_id=unit["coverage_id"])
    return added, duplicates


def read_entries(path: str) -> list:
    try:
        entries = common.read_json(Path(path))
    except (OSError, ValueError) as exc:
        common.fail("invalid", entry=None, errors=[f"cannot read {path}: {type(exc).__name__}"])
    if not isinstance(entries, list):
        common.fail("invalid", entry=None, errors=["expected a JSON array"])
    return entries


def add(run: Path, state: dict, path: str) -> dict:
    entries = read_entries(path)
    for index, entry in enumerate(entries):
        if errors := entry_errors(entry, refs_given=True):
            common.fail("invalid", entry=index, errors=errors)
    ledger = common.read_ledger(run)
    seen = {u["coverage_id"] for u in ledger}
    new = []
    for entry in entries:
        refs = entry.get("canonical_refs") or {k: entry[k] for k in LABELS}
        unit = new_unit(refs, entry, entry.get("prior_status") or ("new" if state.get("prior") else "none"))
        if unit["coverage_id"] in seen:
            common.fail("duplicate", coverage_id=unit["coverage_id"])
        seen.add(unit["coverage_id"])
        if entry.get("status", "planned") != "planned":
            unit.update(status=entry["status"], unresolved=[entry["reason"]])
        new.append(unit)
    ledger += new
    common.write_ledger(run, Path(state["skill"]), ledger)
    common.sync_companions(run, ledger)
    return {"added": [u["coverage_id"] for u in new]}


def update(run: Path, state: dict, path: str) -> dict:
    entries = read_entries(path)
    ledger = common.read_ledger(run)
    known = {u["coverage_id"]: u for u in ledger}
    held = {i for b in state["briefs"].values() if b.get("state") == "open" for i in b.get("units") or []}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) - {"coverage_id", *SAME_FIELDS} or "coverage_id" not in entry:
            common.fail("invalid", entry=index, errors=["expected coverage_id and only starting_paths and block fields"])
        if errors := field_errors(entry):
            common.fail("invalid", entry=index, errors=errors)
        unit = known.get(entry["coverage_id"])
        if unit is None:
            common.fail("unknown-unit", coverage_id=entry["coverage_id"])
        if unit["status"] != "planned" or unit["coverage_id"] in held:
            common.fail("not-planned", coverage_id=unit["coverage_id"], status=unit["status"])
    for entry in entries:
        known[entry["coverage_id"]].update({key: entry[key] for key in SAME_FIELDS if key in entry})
    common.write_ledger(run, Path(state["skill"]), ledger)
    common.sync_companions(run, ledger)
    return {"updated": [e["coverage_id"] for e in entries]}


def cmd_units(args: argparse.Namespace) -> int:
    def body(run: Path, state: dict | None) -> dict:
        assert state is not None
        return add(run, state, args.add) if args.add else update(run, state, args.update)

    return common.run_step(args, body)
