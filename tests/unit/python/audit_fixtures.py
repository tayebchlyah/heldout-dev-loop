"""Fixtures of the `audit-run` tests: a stub `security-audit` skill folder with marker texts and stub validators."""

from __future__ import annotations

import json
from pathlib import Path

FENCE = "```"

HUNTING = f"""# Hunting

## Required hunter prompt

Intro text.

#### Core hunting method - include in every hunter prompt

{FENCE}text
CORE-HUNTING-TEXT
## Inner fenced heading
INNER-AFTER-TEXT
{FENCE}

#### Promotion procedure - copy into every hunter prompt

{FENCE}text
PROMOTION-TEXT
{FENCE}

#### Core validation rules - include in every hunter prompt

{FENCE}text
VALIDATION-RULES-TEXT
{FENCE}

## Structured hunter result

STRUCTURED-RESULT-TEXT

## Parent consolidation and ledger update

CONSOLIDATION-TEXT

## Coverage-critic waves

CRITIC-WAVES-TEXT
"""

VALIDATION = f"""# Validation and reporting

### Phase 3: validate

#### Candidate-verifier prompt

{FENCE}text
VERIFIER-PROMPT-TEXT
{FENCE}

Copy this promotion procedure verbatim:

{FENCE}text
VERIFIER-PROMOTION-TEXT
{FENCE}

### Phase 4: findings

PHASE-4-TEXT

### Phase 5: Verify the final records

PHASE-5-TEXT

### Phase 6: report

PHASE-6-TEXT
"""

ATTACK = """# Attack Classes

**Access control** (subagent_type: general)

ACCESS-CONTROL-TEXT

**Injection**

INJECTION-TEXT
"""

AI = """# AI and LLM

## Core discipline (include in every agent prompt)

AI-CORE-TEXT

## Universal moves

AI-MOVES-TEXT

## Validation rules

AI-VALIDATION-TEXT
"""

VALIDATOR = """const fs = require("fs");
const path = require("path");
const name = path.basename(__filename);
fs.appendFileSync(path.join(__dirname, name + ".log"), process.argv[2] + "\\n");
const exitFile = path.join(__dirname, name + ".exit");
if (fs.existsSync(exitFile)) {
  const code = parseInt(fs.readFileSync(exitFile, "utf8"), 10);
  if (code) { console.error(name + " stub failure"); process.exit(code); }
}
"""


def stub_skill(folder: Path) -> None:
    """Write the stub skill: the headings, fences and blocks the steps read, a schema, two logging validators."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "HUNTING.md").write_text(HUNTING)
    (folder / "VALIDATION-AND-REPORTING.md").write_text(VALIDATION)
    (folder / "ATTACK-CLASSES.md").write_text(ATTACK)
    (folder / "AI-AND-LLM.md").write_text(AI)
    branches = [{"description": f"BRANCH-{verdict}", "properties": {"verdict": {"const": verdict}},
                 "required": ["verdict", "fingerprint"]} for verdict in ("confirmed", "needs_validation", "rejected")]
    (folder / "report-schema.json").write_text(json.dumps({"type": "array", "items": {"oneOf": branches}}, indent=2))
    for script in ("validate-findings.cjs", "validate-coverage-ledger.cjs"):
        (folder / script).write_text(VALIDATOR)


def validator_exit(skill: Path, script: str, code: int) -> None:
    """Make the stub validator `script` exit with `code`."""
    (skill / f"{script}.exit").write_text(str(code))


def validator_calls(skill: Path, script: str) -> list[str]:
    """The target path of each call of the stub validator `script`, in order."""
    log = skill / f"{script}.log"
    return log.read_text().splitlines() if log.exists() else []
