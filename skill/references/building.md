# Building rules

Read before writing code. Four parts: principles, code volume, scope for your harness, test rules in [tests.md](tests.md).

## Principles

Source: pstack 0.15.32 principle skills (https://github.com/cursor/plugins/tree/main/pstack, commit `b1a1298`), `pstack/skills/principle-<name>/SKILL.md`, one line each, the "apply when" from its `description`. Each rule applies when its trigger holds. Where a rule names an incident, it is a real mistake in a project that runs dev-loop (couchbase-doctor) that the rule would have stopped. The Stage 7 merge danger ladder in SKILL.md is copied from `pstack/skills/blast-radius/SKILL.md`, same commit.

1. **Prove it works.** Apply before you call a task done. Check the real thing (run it, read the actual value, read the diff), not a proxy, a self-report or "it compiles". Script the check when you can.
2. **Fix root causes.** Apply when debugging. Trace the symptom to the first place the system went wrong and fix it there. A guard is a fix only when the state it handles is legitimate.
3. **Attack the premise.** Apply when two or more fixes that share one premise failed the same gate. Write the premise down and test it before a third fix.
4. **Make operations idempotent.** Apply to a command, lifecycle step or loop that can crash, restart or retry. A rerun after a partial run reaches the same end state. Incident: the F17a backfill crashed on two legacy documents after Terraform had already replaced the indexes, and prod was repaired by hand.
5. **Migrate callers, then delete the legacy API.** Apply when a new internal API replaces an old one. Move every caller and delete the old API in the same change, no compatibility shim. Incident: F14b added re-export shims that S18 removed two days later with its own spec and plan.
6. **Boundary discipline.** Apply when you wire validation, error handling or a framework adapter. Validate at the boundary (CLI, config, network, external API), trust internal types, keep business logic in pure functions.
7. **Separate before serializing shared state.** Apply when concurrent actors can write the same file, branch, key or state object. Give each actor its own target first. Use a lock only when one shared writer is a real invariant.
8. **Explain the number.** Apply before you trust, report or act on a number you measured. Name what limits it and rule out that it measured something else. For a performance number, run [benchmark.md](benchmark.md). Incident: the F6d to F6g speed targets were missed or amended, and their budgets were raised within a week.

## pstack license

MIT License

Copyright (c) 2026 Lauren Tan

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

## Code volume

Source: Ponytail v5.1.0 `AGENTS.md` (https://github.com/DietrichGebert/ponytail, commit `9b58c1f`), copied verbatim, first-person ponytail-repo line dropped.

### Before you write

Read the task and the code it touches. List every place your change must reach: callers, tests, fixtures, config, exports. Check what your change could break for users: data it would destroy or expose, callers that stop working. That is scope. Extra features are not.

### The smallest complete change

Take the first option that fully works:

1. Does it need to exist? Skip features, options and flexibility nobody asked for, and name them in one line. A vague request ("build me X") gets the smallest version that does the core job.
2. Already in this codebase (a helper, component, service, pattern)? Use it the way the surrounding code does.
3. Standard library or a platform feature? Use it, unless the project has its own. A house component beats a native widget.
4. An installed dependency? Use it. Never add a dependency for a few lines.
5. Can it be one line a reader gets at a glance? One line.
6. Otherwise: the minimum code that works.

- Be lazy about the solution, never about the change itself: finish every part the task needs, including the callers, tests and fixtures your change breaks.
- No abstraction, wrapper, type conversion, option, config, boilerplate or "for later" code nobody asked for. Keep values in the form the platform already gives you. Deletion beats addition. Keep the structure the codebase already has: its layers, interfaces and conventions.
- The shortest working diff wins, once you know everything it must touch. A one-liner that needs decoding is not short.
- Comment only the why the code cannot show, in one line.
- Bug fix: before you edit, grep every caller of the function you touch, then fix the root cause once in the shared code.
- Code you move or merge keeps its error handling and validation.
- Between options of equal size, take the one that is correct on edge cases.
- Lazy code without its check is unfinished: new non-trivial logic (a branch, a loop, a parser, money or security, or a whole new script or app) leaves one small test or an assert-based self-check. Trivial changes need none.
- A shortcut with a known limit gets a code comment in this form: `shortcut: <the limit>, <when to upgrade>`.

Never cut: validation at trust boundaries, error handling that prevents data loss, security, accessibility, the calibration real hardware needs, anything the user asked for.

## Ponytail license

MIT License

Copyright (c) 2026 DietrichGebert

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

## Scope, Claude Code

Vendor text, verbatim. Read the block for your model.

Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1, section "Keep changes and tests to what the task asks for" (punctuation adapted: two em-dashes replaced by commas).

```text
If, while working or testing, you find a pre-existing bug, a performance concern, or behavior the task doesn't mention, don't fix, optimize or extend it in this change unless the requested behavior cannot work without it; report it as a follow-up in your summary. Where the task is ambiguous, implement the reading its wording and the surrounding code most directly support, state that assumption in your summary, and don't build for the other readings as well. Verify your work however you like; scratch scripts and quick checks need not be kept. Commit tests only where the task asks for them or this repository already keeps tests for this kind of change, sized like the neighboring test files, roughly one focused test per stated behavior, and don't turn scratch checks into additional permanent test files. This is about extras only: implement every behavior the task asks for, completely.
```

Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5-5, section "Steer initiative and scope", second paragraph of the prompt.

```text
When the work the user asked for is done and checked, stop and report. Don't add features, tests, files, docs or refactors that weren't asked for. If you think one would help, mention it at the end instead of doing it.
```

## Scope, Codex

Source: https://developers.openai.com/api/docs/guides/latest-model

- Do not write tests for reversible, low-impact changes that mirror the implementation.
- The user's instructions take precedence over guidelines provided in a skill.
- ask for approval only after preparing a concrete, reviewable result.
