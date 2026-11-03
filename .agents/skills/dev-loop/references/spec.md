# Spec template

Stage 2 writes the spec from this template to `Spec.md` in the feature folder `$NOTES/Features/<YYYYMMDD> <Title>/`. Size target: 1,500 words for Goal, Non-goals, Interfaces and seams, Acceptance oracle, Acceptance flows and Supersedes (SKILL.md Stage 2). Then the builder check. Track L: threat model too. A one-way door in the spec: doubt review. Licenses at the end.

## Template

```markdown
---
type: spec
feature: <slug>
track: S|M|L
acceptance:
review_status: draft
shipped:
pr:
---

# <Title>

## Sources
Links only: the framing note (SKILL.md Stage 1), the map decisions this spec relies on, research.

## Goal
One paragraph: what the user gets.

## Non-goals
What this slice does not do, one line each, at least one. Stage 6 triage drops a finding that asks for one. `spec-tester` writes no test for a non-goal.

## Decisions
One row per decision: ID, topic, decision, one line each. Mark each one-way door. The reasoning stays in the framing note.

## Interfaces and seams
Public interfaces: names, parameter types, return types. `ui` in stacks: routes and accessible names.
Seams under test. Prefer an existing seam, the highest one. The ideal number of seams is one.
Under each seam: `Catches:` one line, what a test at this seam catches. `Misses:` one line, what it cannot see.

## Acceptance oracle
One line per observable behaviour. Each line testable, quoted later by tests and reviewers.
`acceptance: regression` or `none`: one line, why no user or system flow changes.
A speed target (time, throughput, size): the line is the claim sentence of [benchmark.md](benchmark.md) "Before you run anything", with the number, its unit, the data set, the run count and the statistic. Whoever measures it runs benchmark.md. The verdict and the number go in the PR body.

## Acceptance flows
One flow per user journey. `spec-tester` writes one test per flow.

## Supersedes
Every earlier `## Acceptance oracle` line this spec contradicts, quoted, each with its replacement. None: "Supersedes: none".

## Decision log
Every decision and surprise of Delivery, one row each, appended by `scripts/devloop.py decide`. Never edited by hand. Shaping choices go in `## Decisions`.

| Time | Stage | Session | Kind | Risk | Decision | Why | Evidence | Result |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
```

`feature`: slug, lower case, words joined by `-`. Branch `<prefix>/<slug>`. `track` from Stage 1. `review_status: approved` only after the user approves. `shipped`, `pr`: empty until Stage 7 merges, then merge date `YYYY-MM-DD` and PR URL. Not `status`: the notes vault owns it.

Seam rule. Source: Matt Pocock skills v1.3.2, `skills/engineering/to-spec/SKILL.md` line 15 (https://github.com/mattpocock/skills, commit `d1478fc`), copied verbatim:

> Sketch out the seams at which you're going to test the feature. Existing seams should be preferred to new ones. Use the highest seam possible. If new seams are needed, propose them at the highest point you can. The fewer seams across the codebase, the better - the ideal number is one.

## Builder check

Spec written: dispatch one fresh subagent, no session context. Claude Code: `general-purpose`. Codex: a generic worker, read-only. Domain: "software engineer". Document: the spec path. Answer each question in the spec, or ask the user, before the approval. "nothing important left": done.

Source: Superpowers 7.0.1, `skills/brainstorming/builder-check-prompt.md` (https://github.com/obra/superpowers, commit `2ce8aa1`), copied verbatim:

```
You are a talented [DOMAIN, e.g. "software engineer", "event planner",
"bakery consultant"]. You've been handed a design document and asked to
put together a plan to build what it describes. You will not be able to
talk to the person who wrote it.

Read [DOCUMENT_PATH].

Report only the questions you'd still need answered before you could plan
this well: places where you'd have to guess and a wrong guess would
matter. For each, give the question and why the answer changes the plan.
Rank them by importance.

Skip anything the document explicitly leaves to the builder, and anything
you could reasonably decide yourself. Skip nitpicks about wording.

If nothing important is left, say "nothing important left".
```

## Track L: threat model

Add `## Threat model` to the spec: per trust boundary, the assets, one STRIDE row per threat that applies, the abuse cases. Each abuse case becomes an `## Acceptance oracle` line.

Source: Addy Osmani agent-skills 0.6.15, `skills/security-and-hardening/SKILL.md`, section "Process: Threat Model First" (https://github.com/addyosmani/agent-skills, commit `8bfcc1f`), copied verbatim, punctuation adapted: four em-dashes replaced by two commas, a colon and a semicolon.

Controls bolted on without a threat model are guesses. Before hardening, spend five minutes thinking like an attacker:

1. **Map the trust boundaries.** Where does untrusted data cross into your system? HTTP requests, form fields, file uploads, webhooks, third-party APIs, message queues, and **LLM output**, plus the local values that look internal because the OS handed them to you: another process's command line or environment, filenames on a shared volume, a path in a job payload. Trust follows who *wrote* a value, not which channel delivered it. Every boundary is attack surface.
2. **Name the assets.** What's worth stealing or breaking? Credentials, PII, payment data, admin actions, money movement.
3. **Run STRIDE over each boundary**: a quick lens, not a ceremony:

| Threat | Ask | Typical mitigation |
|---|---|---|
| **S**poofing | Can someone impersonate a user/service? | Authentication, signature verification |
| **T**ampering | Can data be altered in transit or at rest? | Integrity checks, parameterized queries, HTTPS |
| **R**epudiation | Can an action be denied later? | Audit logging of security events |
| **I**nformation disclosure | Can data leak? | Encryption, field allowlists, generic errors |
| **D**enial of service | Can it be overwhelmed? | Rate limiting, input size caps, timeouts |
| **E**levation of privilege | Can a user gain rights they shouldn't? | Authorization checks, least privilege |

4. **Write abuse cases next to use cases.** For each feature, ask "how would I misuse this?", then make that your first test.

If you can't name the trust boundaries for a feature, you're not ready to secure it. This is OWASP **A04: Insecure Design**; most breaches begin in design, not code.

## One-way door: doubt review

Each decision marked one-way door, any track: one fresh subagent, no session context, gets the prompt below. ARTIFACT: the decision row plus its constraints, 3 to 5 sentences, then each caller, guard or test that contradicts it (outside the cap). CONTRACT: the oracle lines and interfaces it must satisfy. Never pass your reasoning; pass the evidence. Classify each finding: contract misread (fix the contract), valid (change the decision), trade-off (a row: `scripts/devloop.py decide --feature <slug> --kind decision`), noise. At most 3 cycles, then ask the user. Delivery runs no doubt review: Stage 2 ran it for each door the spec marks, and a one-way door the approved spec does not cover is a stop item (SKILL.md autonomy grant).

Source: Addy Osmani agent-skills 0.6.15, `skills/doubt-driven-development/SKILL.md`, Step 3 "DOUBT" (https://github.com/addyosmani/agent-skills, commit `8bfcc1f`), core reviewer prompt and its rule, copied verbatim:

```
Adversarial review. Find what is wrong with this artifact.
Assume the author is overconfident. Look for:
- Unstated assumptions
- Edge cases not handled
- Hidden coupling or shared state
- Ways the contract could be violated
- Existing conventions this might break
- Failure modes under unexpected input

Do NOT validate. Do NOT summarize. Find issues, or state
explicitly that you cannot find any after thorough examination.

ARTIFACT: <paste artifact>
CONTRACT: <paste contract>
```

**Pass ARTIFACT + CONTRACT only. Do NOT pass the CLAIM.** Handing the reviewer your conclusion biases it toward agreement. The reviewer must independently determine whether the artifact satisfies the contract.

## Licenses

The three sources above use the same MIT License text, with these copyright lines:

- Matt Pocock skills: Copyright (c) 2026 Matt Pocock
- Superpowers: Copyright (c) 2025 Jesse Vincent
- Addy Osmani agent-skills: Copyright (c) 2025 Addy Osmani

MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
