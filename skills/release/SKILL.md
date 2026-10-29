---
name: release
description: Run a full release or release candidate: version, notes, audit, one release PR, tag, check of the published release. User-invoked only.
disable-model-invocation: true
---

# Release

User asked for a release: `/release [--rc]` (Codex `$release [--rc]`). Do this, in order. CLI: `python3 .agents/skills/dev-loop/scripts/devloop.py <command>`. `<repo name>`: the repo name in `git remote get-url origin`, no `.git`. Mode: `dev-loop.json` `release_notes`, no key: `cliff`.

Release command (`commands.release_prepare`, `commands.release_check`, `commands.release_verify` in `dev-loop.json`, each optional): run only when mapped, as `RELEASE_VERSION=<version> RELEASE_TAG=<tag> python3 .agents/skills/dev-loop/scripts/devloop.py logged --name <key> -- sh -c '<command>'`. `<version>`: `X.Y.Z` or `X.Y.Z-rc.N`; `<tag>`: `v<version>`. Exit not 0: stop; the report names the command and its `log`.

1. Start. Main checkout, on `main`: `git fetch origin`, `git rebase origin/main`. Local commits or changes on `main`: stop, report them. Run `release-check --sha HEAD`: a reason `open-security` or `unparsed-security`: stop, report it. List the open features from `status` (its `features`).
2. Version. Read the last final tag (`vX.Y.Z`) and the changes after it. Propose major, minor or patch: a change that makes an existing install fail without a user step is major. `--rc`: `X.Y.Z-rc.N`, N one above the highest rc tag of `X.Y.Z`, else 1. A version file (`version_files`) that already holds the target version and no tag for it: a resumed release; the later steps only do what is left.
3. Audit, before any version change. `A` = `HEAD`. `release-check --sha A` with `ok` true: no new audit. Else follow the steps of `skills/release-audit/SKILL.md` (installed at `.agents/skills/release-audit/SKILL.md`) in this session; then `release-check --sha A` must give `ok` true. A confirmed finding: stop. After the fix slice merges, `/release` starts again at step 1.
4. Release branch `release/<tag>` from `A`, in its own worktree: `git worktree add -b release/<tag> <folder> A`. All of steps 4 to 6 run there.
   - Setup: the same as a new worktree of a feature: the dependency install the project's `AGENTS.md` names for a new worktree, before the direct gate.
   - No `version_files` key in `dev-loop.json` (first run): read the repo, propose the list of files that hold the version, write the key. `[]` when none.
   - Set the version in each version file.
   - Notes. `written`: follow the steps of `skills/release-notes/SKILL.md` for `<version>`. `cliff`: `git cliff --tag-pattern '^v[0-9]+[.][0-9]+[.][0-9]+$' --tag <tag> -o CHANGELOG.md`.
   - Run `commands.release_prepare`; its changes go into the release commit.
   - One commit, subject `🔖(release) <repo name> <version>`.
5. Gate. Run the direct gate: `gate --summary` (`commands.gate`, else `scripts/gate.sh`). CI may skip this PR, so the guard tests run here. `exit` not 0: stop, report it. Then run `commands.release_check`; keep its output for the PR body.
6. PR. `git push -u origin release/<tag>`, `gh pr create`. Body: the version, the notes section (`release-notes --tag <tag> --output <file>`, the text of `<file>`), the `release_check` output, the open features. Then `ci wait`: a verdict other than `pass`: stop, report it.
   - Project CI that never calls `ci-skip`: the report says so and shows the job below. Do not edit the workflow.
7. Stop 1. Show the version, the notes and `version_files`. Wait for the user to approve them. Then `gh pr merge --rebase --match-head-commit <sha>`, `<sha>` the release commit (`git rev-parse HEAD` in the release worktree): a head moved after the approval is refused. Main checkout: `git fetch origin`, `git rebase origin/main`. `release-check --sha HEAD` must give `ok` true (the release commit after `A` keeps the audit current). `stale-audit` (another PR merged after `A`): stop, report it; the next `/release` runs a new audit at step 3.
8. Stop 2. Wait for the user to confirm the tag push and, when no workflow under `.github/workflows/` runs on a tag push, the GitHub Release creation. Then `git tag -a <tag> -m "<repo name> <version>"`, `git push origin <tag>`. No tag workflow: `gh release create <tag> --notes-file <file>`, `--prerelease` for an rc. `<file>`: `written`: `release-notes --tag <tag> --output <file>`; `cliff`: the output of `git cliff --latest --strip header`.
9. Check. A tag workflow: wait for its run of the tag (`gh run list`, `gh run watch`); it must succeed. `gh release view <tag>`: in `written` mode its body equals the text of the `<file>` that `release-notes --tag <tag> --output <file>` wrote. Run `commands.release_verify` in the main checkout.
10. Report: the tag, the release URL, the gate, the `release_check` and `release_verify` results, the open features.

Never a merge before stop 1. Never a tag push or a release creation before stop 2.

## CI skip job

A release PR or a kit upgrade PR changes no code. This job lets the project CI skip the other jobs for it. It runs the `ci-skip` of the base kit, never the kit of the PR. A kit upgrade PR skips only in the kit-source repo.

```yaml
  changes:
    runs-on: ubuntu-latest
    outputs:
      skip: ${{ steps.skip.outputs.skip }}
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          ref: ${{ github.event.pull_request.head.sha || github.sha }}
          fetch-depth: 0
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          ref: ${{ github.event.pull_request.base.sha || github.event.before }}
          path: .devloop-base
      - id: skip
        run: |
          if [ ! -f .devloop-base/.agents/skills/dev-loop/scripts/ci_skip.py ]; then
            echo "skip=false" >> "$GITHUB_OUTPUT"
          else
            python3 .devloop-base/.agents/skills/dev-loop/scripts/devloop.py ci-skip --repo . --base "${{ github.event.pull_request.base.sha || github.event.before }}" --github-output
          fi
```

Each other job:

```yaml
    needs: changes
    if: ${{ !cancelled() && needs.changes.outputs.skip != 'true' }}
```
