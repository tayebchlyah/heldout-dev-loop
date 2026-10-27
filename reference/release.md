# Release

A cycle: release candidates `vX.Y.Z-rc.N` (N from 1), then the final `vX.Y.Z`. This repo's kit moves to each tag by an upgrade PR, so the next feature runs on it.

## Version choice

At rc.1 of a cycle, the agent proposes major, minor or patch from the changes after the last final release: a change that makes an existing install fail without a user step is major. The user approves it in the rc PR. A later breaking feature can raise it in the cycle (`2.2.0-rc.3` to `3.0.0-rc.1`). This cycle: `3.0.0`.

## When to cut a release candidate

After a merge, the Stage 7 report has one line `rc: now|later: <reason>`. The agent compares the kit-source commits after the last tag (rc or final) with the planned work: open GitHub issues, specs in `$NOTES/specs/` with no `shipped:`, framing notes with no spec, open `## Deferred` items. `now`: a planned item needs the change in the kit before it starts; the reason names it. `later`: no planned item needs it now. Judgment, no script. The user decides to cut.

## Changelog

`CHANGELOG.md`: one section per final release. During a cycle it also has one section `## [X.Y.Z-rc.N]` for the newest rc, with all commits after the last final release. The final release replaces that section with `## [X.Y.Z]`. The pattern makes rc tags not count as tags.

- rc: `GITHUB_TOKEN=$(gh auth token) git cliff --tag-pattern '^v[0-9]+[.][0-9]+[.][0-9]+$' --tag vX.Y.Z-rc.N -o CHANGELOG.md`
- Final: `GITHUB_TOKEN=$(gh auth token) git cliff --tag-pattern '^v[0-9]+[.][0-9]+[.][0-9]+$' --tag vX.Y.Z -o CHANGELOG.md`

Release notes (the workflow): an rc body has the commits after the previous tag, rc or final; a final body the commits after the previous final tag.

## Release candidate

1. rc PR: set `package.json` `version` to `X.Y.Z-rc.N`, write `CHANGELOG.md` (rc command above).
2. Rebase the PR, no merge commit.
3. On `main`, at that commit: `/release-audit` (Codex `$release-audit`, main session), then `python3 .agents/skills/dev-loop/scripts/devloop.py release-check --sha HEAD` must give `ok` true. A confirmed finding gets a fix slice, then a new audit of that change. The git guard runs `release-check` on the tag push and shows its reasons.
4. The user confirms, then: `git tag -a vX.Y.Z-rc.N -m "dev-loop vX.Y.Z-rc.N"`, `git push origin vX.Y.Z-rc.N`. The workflow makes a GitHub pre-release.
5. Upgrade PR: on a branch, `npx github:tchlyah/dev-loop#vX.Y.Z-rc.N upgrade`, rebase merge.
6. On `main` in the main checkout: `git fetch origin`, `git rebase origin/main`.
7. The next feature starts in a new session.

## Final release

1. Release PR: set `package.json` `version` to `X.Y.Z`, write `CHANGELOG.md` (final command above).
2. Run `scripts/canary.sh`, paste its lines into the release PR.
3. Rebase the PR, no merge commit.
4. On `main`: `git tag -a vX.Y.Z -m "dev-loop vX.Y.Z"` (local), then `python3 scripts/repo_rules.py promoted --tag vX.Y.Z`: the final tag ships the kit source of its last rc. A failure: delete the local tag, cut a new rc.
5. `/release-audit` (Codex `$release-audit`, main session), then `python3 .agents/skills/dev-loop/scripts/devloop.py release-check --sha HEAD` must give `ok` true.
6. `git push origin vX.Y.Z`. One tag per push: GitHub sends no push event for more than three tags. The workflow checks the version, runs `promoted` again (fails there on a kit source change after the last rc), makes the notes and the GitHub Release.
7. Upgrade PR: on a branch, `npx github:tchlyah/dev-loop#vX.Y.Z upgrade`, rebase merge.
