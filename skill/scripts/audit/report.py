"""`audit-run report`: `REPORT.md`, `FINDINGS-DETAIL.md` and `NEEDS-VALIDATION.md` from `findings.json`, the ledger,
the run metadata and the parent's summary file."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from audit import common
from release import one_line

DETAIL_SEVERITIES = ("medium", "high", "critical")


def text(value: object) -> str:
    return one_line(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True))


def severity(record: dict) -> str:
    value = record.get("severity")
    return text(value.get("overall_severity", "")) if isinstance(value, dict) else text(value or "")


def trace(record: dict) -> list[str]:
    return [f"  - {text(t.get('file', ''))}:{text(t.get('line', ''))} {text(t.get('scope', ''))}".rstrip()
            for t in record.get("trace") or [] if isinstance(t, dict)]


def confirmed_block(n: int, record: dict) -> list[str]:
    lines = [f"### Finding {n}: {text(record.get('title', ''))}", "", f"- Fingerprint: `{text(record['fingerprint'])}`",
             f"- Severity: {severity(record)}", "- Trace:", *trace(record)]
    for key, label in (("conditions", "Conditions"), ("execution", "Execution"), ("observed_result", "Observed result"),
                       ("remediation", "Remediation")):
        if key in record:
            lines.append(f"- {label}: {text(record[key])}")
    return lines + [""]


def validation_block(n: int, record: dict) -> list[str]:
    lines = [f"### Needs validation {n}: {text(record.get('title', ''))}", "",
             f"- Fingerprint: `{text(record['fingerprint'])}`", "- Trace:", *trace(record)]
    for key, label in (("blockers", "Blockers"), ("validation_plan", "Validation plan")):
        if key in record:
            lines.append(f"- {label}: {text(record[key])}")
    return lines + [""]


def report_text(metadata: dict, records: list[dict], ledger: list[dict], state: dict, summary: str) -> str:
    confirmed = [r for r in records if r.get("verdict") == "confirmed"]
    pending = [r for r in records if r.get("verdict") == "needs_validation"]
    counts = Counter(u["status"] for u in ledger)
    lines = [f"# {text(metadata.get('run_id', ''))} security audit report", "", "## Run", ""]
    lines += [f"- Run: {text(metadata.get('run_id', ''))}", f"- Profile: {text(metadata.get('profile', ''))}",
              f"- Scope: {text((metadata.get('scope_paths') or {}).get('scope', ''))}",
              f"- Budget: {text(metadata.get('budget'))}",
              f"- Source commit: `{text((metadata.get('source_ref') or {}).get('commit', ''))}`",
              f"- Run status: {text(metadata.get('run_status', ''))}"]
    if metadata.get("incomplete_reason"):
        lines.append(f"- Incomplete reason: {text(metadata['incomplete_reason'])}")
    lines += [f"- Execution policy: {text(metadata.get('execution_policy', ''))}",
              f"- Prior runs: {text(', '.join(Path(p).name for p in metadata.get('prior_run_paths') or []) or 'none')}",
              f"- Deferred units: {counts['deferred']}; out of scope: {counts['out_of_scope']}"]
    for status in ("prior_confirmed_same_source", "prior_confirmed_changed_source"):
        ids = [text(u["coverage_id"]) for u in ledger if u.get("prior_status") == status]
        lines.append(f"- Carried {status}: {', '.join(ids) or 'none'}")
    lines += ["", "## Security posture", "", summary.rstrip("\n"), "", "## Confirmed findings", ""]
    lines += [f"- {severity(r)}: {text(r.get('title', ''))} (`{text(r['fingerprint'])}`). "
              f"Observed: {text(r.get('observed_result', ''))}" for r in confirmed] or ["None."]
    lines += ["", "## Confirmed finding details", ""]
    for n, record in enumerate(confirmed, 1):
        lines += confirmed_block(n, record)
    lines += ["None.", ""] if not confirmed else []
    lines += ["## Needs validation", ""]
    for n, record in enumerate(pending, 1):
        lines += validation_block(n, record)
    lines += ["None.", ""] if not pending else []
    notes = sorted({text(h) for u in ledger for h in u.get("hardening") or [] if text(h)})
    lines += ["## Hardening notes", "", *[f"- {note}" for note in notes], "", "## Coverage", ""]
    lines += [f"- {status}: {n}" for status, n in sorted(counts.items())]
    for unit in ledger:
        if unit["status"] in ("deferred", "out_of_scope"):
            lines.append(f"- {unit['status']} `{text(unit['coverage_id'])}`: {text('; '.join(unit.get('unresolved') or []))}")
    last = state["critics"][-1] if state["critics"] else None
    lines.append(f"- Last critic: {text({k: last[k] for k in ('final', 'added', 'reassigned')}) if last else 'none'}")
    return "\n".join(lines) + "\n"


def cmd_report(args: argparse.Namespace) -> int:
    def body(run: Path, state: dict | None) -> dict:
        assert state is not None
        metadata = common.read_json(run / common.METADATA)
        if metadata.get("run_status") not in ("complete", "incomplete"):
            common.fail("not-closed", run_status=metadata.get("run_status"))
        try:
            summary = Path(args.summary).read_text(encoding="utf-8")
        except (OSError, ValueError):
            common.fail("no-summary", file=args.summary)
        records = sorted(common.read_json(run / common.FINDINGS), key=lambda r: r["fingerprint"])
        ledger = common.read_ledger(run)
        confirmed = [r for r in records if r.get("verdict") == "confirmed"]
        detail = ["# Confirmed finding details", ""]
        for n, record in enumerate(confirmed, 1):
            if severity(record) in DETAIL_SEVERITIES:
                detail += confirmed_block(n, record)
        pending = ["# Needs validation", ""]
        for n, record in enumerate((r for r in records if r.get("verdict") == "needs_validation"), 1):
            pending += validation_block(n, record)
        files = {"REPORT.md": report_text(metadata, records, ledger, state, summary),
                 "FINDINGS-DETAIL.md": "\n".join(detail) + "\n", "NEEDS-VALIDATION.md": "\n".join(pending) + "\n"}
        for name, body_text in files.items():
            common.write_text_atomic(run / name, body_text)
        return {"files": [str(run / name) for name in files]}

    return common.run_step(args, body)
