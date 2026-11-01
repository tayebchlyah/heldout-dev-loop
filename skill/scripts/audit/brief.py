"""`audit-run brief`: one agent brief: its id, its folder, its prompt from the parent files and the skill text, and the
units or fingerprint it holds."""

from __future__ import annotations

import argparse
import json
import os
import shlex
from pathlib import Path

import devloop
from audit import common, safefs, skilltext
from release import one_line

KIND_LETTER = {"hunter": "h", "critic": "c", "verifier": "v", "recheck": "r"}
VERIFIER_OWNED = ("prior_confirmed_same_source", "prior_needs_validation")
VALIDATION = "VALIDATION-AND-REPORTING.md"
UNIT_FIELDS = ("coverage_id", "surface", "boundary", "subsystem", "attack_class", "canonical_refs", "starting_paths",
               "ordinary_attack_class_block", "selected_companion_blocks", "excluded_blocks", "prior_status", "wave")
PREAMBLE = {
    "hunter": "Your goal is to find source-grounded security invariant failures in your assigned coverage units. Write "
              "exactly one JSON object that matches the structured-result contract at the end of this prompt to the "
              "result path below, then run the check command and fix every error it reports.",
    "critic": "Your goal is to find coverage gaps in the ledger below: you propose coverage, not findings. Write exactly "
              "one JSON object that matches the critic result contract below to the result path below, then run the "
              "check command and fix every error it reports.",
    "verifier": "You did not write this candidate: your goal is to try to refute it from repository source and bounded "
                "local evidence. Write exactly one JSON object `{\"decision\", \"record\", \"checks\"}` to the result "
                "path below, then run the check command and fix every error it reports.",
    "recheck": "Your goal is to verify the final record below with fresh eyes, inside source and local boundaries. Write "
               "exactly one JSON object, `{\"decision\": \"verified\", \"fingerprint\"}` or `{\"decision\": "
               "\"replace\", \"reason\", \"record\"}` with optional `checks`, to the result path below, then run the "
               "check command and fix every error it reports.",
}
NOTES_RULE = ("Add `notes`: an object from each `reassign_ids` coverage id and each `resolved_prior_leads` fingerprint "
              "to its source-backed reason. An entry with no note is invalid.")


def check_line(run: Path, repo: str, agent: str) -> str:
    """The shell line an agent runs to check its own result."""
    script = Path(devloop.__file__).resolve().parent / "devloop.py"
    return " ".join(shlex.quote(word) for word in (
        "python3", str(script), "audit-run", "check", "--run", str(run), "--agent", agent, "--repo", repo))


def carried_units(ledger: list[dict], fp: str) -> list[dict]:
    """The in-scope carried units a verifier of `fp` owns."""
    return [u for u in ledger if fp in (u.get("prior_fingerprints") or []) and u.get("prior_status") in VERIFIER_OWNED
            and u["status"] != "out_of_scope"]


def limits(agent: str) -> str:
    return (f"Write only under `agents/{agent}/scratch/`. The parent promotes each file a `local` check names, "
            f"`agents/{agent}/artifacts/<rel>` from `agents/{agent}/scratch/<rel>`: at most 256 KiB per file, 1 MiB per "
            "agent. Result file at most 5 MiB.")


def as_json(value: object) -> str:
    return "```json\n" + json.dumps(value, indent=2, ensure_ascii=False) + "\n```"


def branches(skill: Path, verdicts: tuple[str, ...]) -> str:
    return "\n\n".join("```json\n" + skilltext.schema_branch(skill, v) + "\n```" for v in verdicts)


def hunter_parts(state: dict, ledger: list[dict], ids: list[str], skill: Path, agent: str) -> list[tuple[str, str]]:
    units = [u for u in ledger if u["coverage_id"] in ids]
    refs: list[str] = []
    for u in units:
        for ref in ([u["ordinary_attack_class_block"]] if u.get("ordinary_attack_class_block") else []) + list(
                u.get("selected_companion_blocks") or []):
            if ref not in refs:
                refs.append(ref)
    excluded = [f"- {e['block']} ({u['coverage_id']}): {e['reason']}" for u in units for e in u.get("excluded_blocks") or []]
    exclusions = [f"- `{fp}`: {one_line(state['prior_records'].get(fp, {}).get('title', ''))}. Root cause: "
                  f"{one_line(state['prior_records'].get(fp, {}).get('root_cause', ''))}"
                  for u in ledger if u.get("prior_status") == "prior_confirmed_same_source"
                  for fp in u.get("prior_fingerprints") or []]
    peers = [f"- {u['coverage_id']}" for u in ledger if u["status"] == "in_progress" and u.get("agent_id") != agent]
    return [
        ("Assigned units", "\n\n".join(as_json({k: u[k] for k in UNIT_FIELDS if k in u}) for u in units)),
        ("Selected blocks", "\n\n".join(skilltext.block(skill, ref) for ref in refs) or "None."),
        ("Excluded blocks", "\n".join(excluded) or "None."),
        ("Core hunting method", skilltext.fences(skill, "HUNTING.md", "#### Core hunting method", 1)[0]),
        ("Promotion procedure", skilltext.fences(skill, "HUNTING.md", "#### Promotion procedure", 1)[0]),
        ("Core validation rules", skilltext.fences(skill, "HUNTING.md", "#### Core validation rules", 1)[0]),
        ("Carried exclusions", "Do not report these prior confirmed findings again:\n" + "\n".join(exclusions)
         if exclusions else "None."),
        ("Peer coverage ids", "\n".join(peers) or "None."),
        ("Structured hunter result", skilltext.section(skill, "HUNTING.md", "## Structured hunter result")),
        ("Record contract", branches(skill, ("confirmed", "needs_validation"))),
    ]


def critic_parts(state: dict, ledger: list[dict], skill: Path) -> list[tuple[str, str]]:
    candidates = [{"fingerprint": fp, "proposed_verdict": c["body"].get("proposed_verdict"),
                   "title": c["body"].get("title"), "hunter": c["hunter"]} for fp, c in sorted(state["candidates"].items())]
    gaps = {"prior_records": {fp: r.get("verdict") for fp, r in sorted(state["prior_records"].items())},
            "prior_units_not_covered": state["prior_gaps"]}
    return [
        ("Coverage-critic waves", skilltext.section(skill, "HUNTING.md", "## Coverage-critic waves")),
        ("Ledger", as_json(ledger)),
        ("Candidates", as_json(candidates)),
        ("Prior gap summary", as_json(gaps)),
        ("Notes", NOTES_RULE),
    ]


def verifier_parts(state: dict, ledger: list[dict], fp: str, skill: Path) -> list[tuple[str, str]]:
    prompt, promotion = skilltext.fences(skill, VALIDATION, "#### Candidate-verifier prompt", 2)
    parts = [("Candidate-verifier prompt", prompt), ("Promotion procedure", promotion)]
    if state["profile"] == "quick":
        parts.append(("Final record check", skilltext.span(skill, VALIDATION, "### Phase 5", "### Phase 6")))
    candidate = state["candidates"].get(fp, {}).get("body") or state["prior_records"].get(fp)
    linked = [u for u in ledger if fp in (u.get("result_fingerprints") or [])]
    later = {a for a, b in state["briefs"].items() if b["kind"] in ("verifier", "recheck")}
    checks = [{"coverage_id": u["coverage_id"],
               "local_checks": [c for c in u.get("local_checks") or [] if c.get("agent_id") not in later]} for u in linked]
    files: list[str] = []
    for u in linked + carried_units(ledger, fp):
        for ref in u.get("selected_companion_blocks") or []:
            name = ref.split("#", 1)[0]
            if name != "ATTACK-CLASSES.md" and name not in files:
                files.append(name)
    prior = state["prior_records"].get(fp)
    parts += [
        ("Candidate", as_json(candidate)),
        ("Linked unit checks and artifacts", as_json(checks)),
        ("Companion validation rules", "\n\n".join(skilltext.block(skill, f"{f}#Validation rules") for f in files)
         or "None."),
        ("Record contract", branches(skill, ("confirmed", "needs_validation", "rejected"))),
        ("Prior records with this fingerprint", as_json([prior]) if prior else "None."),
    ]
    return parts


def recheck_parts(state: dict, fp: str, skill: Path) -> list[tuple[str, str]]:
    return [
        ("Final record check", skilltext.span(skill, VALIDATION, "### Phase 5", "### Phase 6")),
        ("Final record", as_json(state["final"][fp])),
        ("Record contract", branches(skill, ("confirmed", "needs_validation", "rejected"))),
        ("Promotion procedure", skilltext.fences(skill, VALIDATION, "#### Candidate-verifier prompt", 2)[1]),
    ]


def parent_file(run: Path, name: str, error: str) -> str:
    path = run / name
    if path.is_symlink() or not path.is_file():
        common.fail(error, file=str(path))
    return path.read_text(encoding="utf-8")


def cmd_brief(args: argparse.Namespace) -> int:
    def body(run: Path, state: dict | None) -> dict:
        assert state is not None
        kind, fp = args.kind, args.fingerprint
        context = parent_file(run, "context.md", "no-context")
        architecture = parent_file(run, "architecture.md", "no-architecture")
        ledger = common.read_ledger(run)
        by_id = {u["coverage_id"]: u for u in ledger}
        held: list[dict] = []
        if kind == "hunter":
            ids = list(dict.fromkeys(args.units))
            if bad := [i for i in ids if i not in by_id or by_id[i]["status"] != "planned"]:
                common.fail("not-planned", units=bad)
            if bad := [i for i in ids if by_id[i].get("prior_status") in VERIFIER_OWNED]:
                common.fail("carried", units=bad)
            held = [by_id[i] for i in ids]
        elif kind == "verifier":
            carried = carried_units(ledger, fp)
            if fp not in state["candidates"] and not carried:
                common.fail("unknown-fingerprint", fingerprint=fp)
            held = [u for u in carried if u["status"] == "planned"]
        elif kind == "recheck" and fp not in state["final"]:
            common.fail("unknown-fingerprint", fingerprint=fp)
        agent = KIND_LETTER[kind] + f"{1 + sum(b['kind'] == kind for b in state['briefs'].values()):02d}"
        skill = Path(state["skill"])
        try:
            if kind == "hunter":
                parts = hunter_parts(state, ledger, [u["coverage_id"] for u in held], skill, agent)
            elif kind == "critic":
                parts = critic_parts(state, ledger, skill)
            elif kind == "verifier":
                parts = verifier_parts(state, ledger, fp, skill)
            else:
                parts = recheck_parts(state, fp, skill)
        except skilltext.SkillChanged as exc:
            skilltext.changed(exc)
        result = run / "agents" / agent / "scratch" / "result.json"
        head = [("Role", PREAMBLE[kind]), ("Run context", context)]
        if kind != "recheck":
            head.append(("Architecture", architecture))
        tail = [("Result path", f"`{result}`\n\nRelative to the run folder: `agents/{agent}/scratch/result.json`")]
        if kind in ("hunter", "verifier"):
            tail.append(("Limits", limits(agent)))
        tail.append(("Check command", f"```sh\n{check_line(run, state['repo'], agent)}\n```"))
        prompt = "\n\n".join(f"## {title}\n\n{text.strip()}" for title, text in head + parts + tail) + "\n"
        folder = run / "agents" / agent
        try:
            agent_fd = safefs.make_agent_folder(run, agent)
        except FileExistsError:
            common.fail("agent-exists", agent=str(folder))
        try:
            try:
                inputs = {"units": [u["coverage_id"] for u in held]} if kind == "hunter" else (
                    {"fingerprint": fp} if fp else {})
                final = bool(args.final)
                meta = json.dumps({"kind": kind, **inputs, "final": final}, ensure_ascii=False) + "\n"
                safefs.create_file(agent_fd, "brief.json", meta.encode("utf-8"))
                safefs.create_file(agent_fd, "prompt.md", prompt.encode("utf-8"))
                for unit in held:
                    unit.update(status="in_progress", agent_id=agent)
                if held:
                    common.write_ledger(run, skill, ledger)
            finally:
                os.close(agent_fd)
        except BaseException:
            safefs.remove_agent_folder(run, agent)
            raise
        state["briefs"][agent] = {"kind": kind, "units": [u["coverage_id"] for u in held], "fingerprint": fp,
                                  "final": final, "state": "open", "order": None, "reason": None}
        common.save_state(run, state)
        return {"agent": agent, "prompt": str(folder / "prompt.md"), "result": str(result)}

    return common.run_step(args, body)
