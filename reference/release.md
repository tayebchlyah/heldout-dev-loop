# Release

A cycle: release candidates `vX.Y.Z-rc.N` (N from 1), then the final `vX.Y.Z`. Each runs through `/release` (Codex `$release`; `skills/release/SKILL.md`). The release commit holds this repo's kit upgraded to its own tag, so the next feature runs on it.

## Version choice

At rc.1 of a cycle, the agent proposes major, minor or patch from the changes after the last final release: a change that makes an existing install fail without a user step is major. The user approves it in the rc PR. A later breaking feature can raise it in the cycle (`2.2.0-rc.3` to `3.0.0-rc.1`). This cycle: `3.0.0`.

## When to cut a release candidate

After a merge, the Stage 7 report has one line `rc: now|later: <reason>`. The agent compares the kit-source commits after the last tag (rc or final) with the planned work: open GitHub issues, specs in `$NOTES/Features/*/Spec.md` with no `shipped:`, framing notes with no spec, open `## Deferred` items. `now`: a planned item needs the change in the kit before it starts; the reason names it. `later`: no planned item needs it now. Judgment, no script. The user decides to cut.

## Changelog

`dev-loop.json` `release_notes` is `written`: `CHANGELOG.md` holds prose, one section per final release, written by `/release-notes` (Codex `$release-notes`) inside `/release`. During a cycle it also has one section `## [X.Y.Z-rc.N]` for the newest rc, with everything after the last final release; it replaces the section of the previous rc. The final release replaces it with `## [X.Y.Z]`. `release.yml` takes the GitHub Release body from that section (`devloop.py release-notes`). `cliff.toml` stays for `scripts/repo_rules.py subjects`.

First written release: in its release PR, run `/release-notes --all` by hand from the kit source (`skills/release-notes/SKILL.md`), then the GitHub Release bodies of the published tags after one confirmation. Run that release from the kit source of `skills/release/SKILL.md` too: the pinned kit gets the two skills only from that release.

## Release commands

`/release` runs the `dev-loop.json` commands of this repo with `RELEASE_VERSION` and `RELEASE_TAG` set:

1. `commands.release_prepare` (`node bin/install.mjs upgrade`): in the release branch, before the release commit. The dogfood upgrade goes into the release PR, so the tagged commit holds its own installed kit. No upgrade PR.
2. `commands.release_check` (`scripts/release-check.sh`): after the release commit. Runs `scripts/canary.sh`; for a final version also `scripts/repo_rules.py promoted --tag "$RELEASE_TAG" --sha HEAD` (`promoted --sha`): the commit ships the kit source of its last rc. Its lines go into the release PR body.
3. `commands.release_verify` (`scripts/release-verify.sh`): after the GitHub Release. A temporary clone of `main` runs `npx github:tchlyah/dev-loop#$RELEASE_TAG upgrade` and must show no diff.

## Release candidate

1. `/release --rc` (Codex `$release --rc`) in the main session, on `main` of the main checkout.
2. It runs `release-check`, and the `/release-audit` steps when no current audit record covers `HEAD`. A confirmed finding gets a fix slice, then `/release --rc` again.
3. It opens the release PR: `package.json` and `package-lock.json` at `X.Y.Z-rc.N`, the rc section, the dogfood upgrade. CI may skip it (job `changes`).
4. The user approves the version and the notes; `/release` rebase merges the PR.
5. The user confirms the tag; `/release` runs `git tag -a vX.Y.Z-rc.N` and `git push origin vX.Y.Z-rc.N`. The git guard runs `release-check` on the tag push. The workflow makes a GitHub pre-release.
6. `/release` checks the release body and runs `commands.release_verify`. The next feature starts in a new session.

## Final release

1. `/release` (Codex `$release`) in the main session, on `main` of the main checkout.
2. Steps as for a release candidate, version `X.Y.Z`. `commands.release_check` also runs `promoted --sha`: a failure means a kit source change after the last rc; cut a new rc.
3. The tag push: one tag per push, GitHub sends no push event for more than three tags. The workflow checks the version, runs `promoted` again, writes the notes from the `## [X.Y.Z]` section and makes the GitHub Release.
