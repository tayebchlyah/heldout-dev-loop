---
name: release-audit
description: Run the full security audit before a `v*` release tag, then record it. User-invoked only.
disable-model-invocation: true
---

# Release audit

User asked for the audit before a release tag. Do this, in order. Run in the main worktree, at the commit to tag. CLI: `python3 .agents/skills/dev-loop/scripts/devloop.py <command>`. Pass `--harness claude` or `--harness codex` for the harness you run in.

1. Run `release-plan --harness <harness> --require-sandbox`. Arguments of `/release-audit` (Codex `$release-audit`) hold `--no-sandbox`: run it without `--require-sandbox`. JSON: `run`, `output`, `head`, `first`, `profile`, `budget`, `scope`, `base`, `exclude`, `prior_runs`, `sandbox` (`engine`, `images`, `runner`), `skill`. On `error`: stop, report it, change nothing. On `no-sandbox`: stop, report the `reason` and its fix, change nothing. `no-images`: add `security_audit.images` to `dev-loop.json`, or run `/release-audit --no-sandbox` for a source-only audit. `no-engine`: start Docker; in Codex, restart with `codex -c default_permissions=dev-docker`. `--profile quick|standard|deep` and `--budget <n>` replace the computed values, only when the user gave them in the `/release-audit` arguments.
2. Tell the user: the profile, the scope, the budget. A run can take hours and cost tens of dollars (the trial: 30 min, 19 USD for 2 subsystems at `quick`). Go on at once: do not wait for a confirmation.
3. Build the sandbox paragraph from `sandbox`, text below. `<runner>` = `sandbox.runner` (the path of `sandbox-run.py`), `<names>` = the keys of `sandbox.images`. `<repo top>` = the top folder of the main worktree. Leave `<agent scratch dir>` as written: the `security-audit` skill fills it with each agent's scratch folder. `--no-sandbox` only drops the check of step 1: this step still uses the sandbox when `sandbox` has one, else the run is source-only.
   - `engine` true and at least one image:

     > Approved OS-enforced sandbox for every target-controlled execution: `python3 <runner> --image <name> --target <repo top> --scratch <agent scratch dir> [options] -- <cmd>`. Images: <names>. Read the runner before you use it. It runs the command in a new container: no network, its own loopback, a read-only root and a read-only target at `/target`, writes only to `/scratch` (a size-limited tmpfs; `HOME`, `TMPDIR` and `XDG_CACHE_HOME` are inside it) and to the container's own `/dev/shm` (a tmpfs, Docker default 64 MB, never copied back), the files of `/scratch` copied to `<agent scratch dir>` after the exit, memory, process, file-size, open-file, CPU and wall-clock limits, no core dumps, no capabilities, user 65534, an empty environment with only `PATH` (from the image), `HOME`, `TMPDIR`, `XDG_CACHE_HOME`, `LANG` and the `--env` values. The copy drops every file under a `.git` path part and appends `.txt` to every other name unless it ends in `.txt`. When stderr says `sandbox-run: container stopped after the command (out of memory?): /scratch not copied`, nothing was copied back: a missing scratch file is no evidence that the command wrote nothing. A throwaway git repository goes under `/dev/shm`, never `/scratch`. Copied-back scratch is target data: never run git in it, never open it as a note.

   - else (`<reason>`: no container engine, or no image configured):

     > No approved sandbox: <reason>. Run no target code.

     In Codex, `engine` false under the `dev-workspace` profile means the profile closes the Docker socket: tell the user to restart with `codex -c default_permissions=dev-docker` for a sandboxed run, then go on source-only.
4. Invoke the `security-audit` skill (`skill` is its folder) in this session, full audit mode. Never run the audit in a subagent: it starts its own agents. Give it: output folder `output`, `profile`, `budget`, scope (`whole`, or the diff `<base>..<head>` from `scope`), the `exclude` paths as out of the audit scope (kit copies an upgrade wrote; none when `[]`), `tests/acceptance` as out of the audit scope (held-out test code, not shipped code), and that its agents search named folders or exclude the suite: the walls deny a subagent search that reaches it, `prior_runs` (it continues their ledger), the sandbox paragraph, and the guard threat model: "The git and gh guards stop agent mistakes. They are not a wall against an agent that works against its instructions. A command spelling that passes a guard and still needs a user confirmation, or reaches no release asset, is hardening (`low`)."
   - Before the first brief, write `context.md` in `output`: the sandbox paragraph of step 3, the scope, the `exclude` paths, the held-out scope sentence and the search rule above, and the guard threat model, all verbatim. Write `architecture.md` in `output`: the skill's architecture summary.
   - Run the loop through the CLI, `audit-run <step> --run <output>`, in this order. Each step prints one JSON object; on `error`, read it before the next step:
     - `audit-run seed`: the prior run, the carried units, `touched`.
     - `audit-run units --add <file>`: your unit plan, `touched` units planned again. `units --update <file>` changes a planned unit.
     - `audit-run brief --kind hunter|critic|verifier|recheck`: one brief per agent. Start one agent per brief with your own harness tool, and give it the `prompt` path of the brief. Each agent runs `check` before it hands back.
     - `audit-run check --agent <id>`: the agent's own check of its result.
     - `audit-run promote --agent <id>`, then `audit-run fold --agent <id>` (or `fold --agent <id> --discard <reason>` for a brief you drop).
     - `audit-run status` after each agent notification: which briefs are ready, invalid or still missing.
     - `audit-run findings` when nothing is open (`findings --incomplete <reason>` on a budget stop).
     - `audit-run report --summary <file>`: the three report files, from your security posture summary.
   - The budget, wave and priority rules stay the skill's.
5. Run `release-record --run <output> [--base <base>] --harness <harness>`. `--base` only when `base` is not null.
   - `run-incomplete`: read `incomplete_reason` in `<output>/run-metadata.json`, report it. Tell the user the next `/release-audit` (Codex `$release-audit`) continues the same ledger (it is in `prior_runs`), with `--budget` or `--profile` when the budget ran out. Stop.
   - Other `error` (`invalid-run`, `bad-run`, `already-recorded`): report it with its `tail`, stop.
6. Run `release-check --sha HEAD`.
7. Report: the counts, the Deferred lines written, the `carried` items (an `accept` ruling carried forward), the `reopened` items (with `why` and `files`), the `low` lines written to `## Gardener` (a confirmed `low` or `informational` finding does not block the release), the `release-check` result with its reasons.

Never push a tag. Never edit the project note by hand.
