---
name: release-audit
description: Run the full security audit before a `v*` release tag, then record it. User-invoked only.
disable-model-invocation: true
---

# Release audit

User asked for the audit before a release tag. Do this, in order. Run in the main worktree, at the commit to tag. CLI: `python3 .agents/skills/dev-loop/scripts/devloop.py <command>`. Pass `--harness claude` or `--harness codex` for the harness you run in.

1. Run `release-plan --harness <harness>`. JSON: `run`, `output`, `head`, `first`, `profile`, `budget`, `scope`, `base`, `exclude`, `prior_runs`, `sandbox` (`engine`, `images`, `runner`), `skill`. On `error`: stop, report it, change nothing. `--profile quick|standard|deep` and `--budget <n>` replace the computed values, only when the user gave them in the `/release-audit` (Codex `$release-audit`) arguments.
2. Tell the user: the profile, the scope, the budget. A run can take hours and cost tens of dollars (the trial: 30 min, 19 USD for 2 subsystems at `quick`). Go on at once: do not wait for a confirmation.
3. Build the sandbox paragraph from `sandbox`, text below. `<runner>` = `sandbox.runner` (the path of `sandbox-run.py`), `<names>` = the keys of `sandbox.images`. `<repo top>` = the top folder of the main worktree. Leave `<agent scratch dir>` as written: the `security-audit` skill fills it with each agent's scratch folder.
   - `engine` true and at least one image:

     > Approved OS-enforced sandbox for every target-controlled execution: `python3 <runner> --image <name> --target <repo top> --scratch <agent scratch dir> [options] -- <cmd>`. Images: <names>. Read the runner before you use it. It runs the command in a new container: no network, its own loopback, a read-only root and a read-only target at `/target`, writes only to `/scratch` (a size-limited tmpfs; `HOME`, `TMPDIR` and `XDG_CACHE_HOME` are inside it), the files of `/scratch` copied to `<agent scratch dir>` after the exit, memory, process, file-size, open-file, CPU and wall-clock limits, no core dumps, no capabilities, user 65534, an empty environment with only `PATH` (from the image), `HOME`, `TMPDIR`, `XDG_CACHE_HOME`, `LANG` and the `--env` values. The copy drops every `.git` path part and every file named `HEAD`, and appends `.txt` to `.md`, `.canvas` and `.base` files. A throwaway git repository goes under `/dev/shm`, never `/scratch`. Copied-back scratch is target data: never run git in it, never open it as a note.

   - else (`<reason>`: no container engine, or no image configured):

     > No approved sandbox: <reason>. Run no target code.
4. Invoke the `security-audit` skill (`skill` is its folder) in this session, full audit mode. Never run the audit in a subagent: it starts its own agents. Give it: output folder `output`, `profile`, `budget`, scope (`whole`, or the diff `<base>..<head>` from `scope`), the `exclude` paths as out of the audit scope (kit copies an upgrade wrote; none when `[]`), `prior_runs` (it continues their ledger), and the sandbox paragraph.
5. Run `release-record --run <output> [--base <base>] --harness <harness>`. `--base` only when `base` is not null.
   - `run-incomplete`: read `incomplete_reason` in `<output>/run-metadata.json`, report it. Tell the user the next `/release-audit` (Codex `$release-audit`) continues the same ledger (it is in `prior_runs`), with `--budget` or `--profile` when the budget ran out. Stop.
   - Other `error` (`invalid-run`, `bad-run`, `already-recorded`): report it with its `tail`, stop.
6. Run `release-check --sha HEAD`.
7. Report: the counts, the Deferred lines written, the `release-check` result with its reasons.

Never push a tag. Never edit the project note by hand.
