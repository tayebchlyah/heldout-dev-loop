#!/usr/bin/env python3
"""Tests for the `audit-run` texts: `release-audit` step 4, the `audit-run` contract, the `AGENTS.md` lines."""

from __future__ import annotations

import json
import unittest

from support import ROOT

STEPS = ["seed", "units --add", "brief", "check", "promote", "fold", "status", "findings", "report"]


class AuditTextTests(unittest.TestCase):
    def test_release_audit_step_4(self) -> None:
        text = (ROOT / "skills" / "release-audit" / "SKILL.md").read_text()
        step4 = text.split("\n4. ")[1].split("\n5. ")[0]
        at = [step4.index(f"audit-run {step}") for step in STEPS]
        self.assertEqual(at, sorted(at))
        for phrase in ["`architecture.md`", "`context.md`", "one agent per brief", "prompt", "after each agent notification",
                       "runs `check` before it hands back"]:
            self.assertIn(phrase, step4, phrase)
        self.assertNotIn(chr(0x2014), text)

    def test_contract_names_every_step_and_error(self) -> None:
        text = (ROOT / "reference" / "cli" / "audit-run.md").read_text()
        for name in ["seed", "units", "brief", "check", "promote", "fold", "findings", "report", "status", "bad-run",
                     "not-seeded", "no-plan", "already-seeded", "invalid", "duplicate", "not-planned", "unknown-unit",
                     "carried", "unknown-fingerprint", "no-architecture", "no-context", "skill-changed", "missing",
                     "unpromoted", "already-folded", "collision", "ledger-invalid", "open", "invalid-run", "not-closed"]:
            self.assertIn(f"`{name}`", text, name)
        source = next(s for s in json.loads((ROOT / "upstream.json").read_text())["sources"] if s["name"] == "security-audit")
        self.assertIn("reference/cli/audit-run.md", source["local"])
        self.assertIn(source["version"], text)

    def test_agents_md_lines(self) -> None:
        agents = (ROOT / "AGENTS.md").read_text()
        self.assertIn("`reference/cli/audit-run.md`", agents)
        self.assertIn("`skill/scripts/audit/`", agents)


if __name__ == "__main__":
    unittest.main()
