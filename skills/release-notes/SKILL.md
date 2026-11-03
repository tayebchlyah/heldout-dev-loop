---
name: release-notes
description: Draft or rewrite a written release notes section of CHANGELOG.md, or every section with --all. User-invoked only.
disable-model-invocation: true
---

# Release notes

User asked for release notes: `/release-notes <version>` or `/release-notes --all` (Codex `$release-notes`). `<version>`: `X.Y.Z` or `X.Y.Z-rc.N`, `<tag>`: `v<version>`. CLI: `python3 .agents/skills/dev-loop/scripts/devloop.py <command>`. `$NOTES`: output of `python3 .agents/skills/dev-loop/scripts/notes_path.py`.

1. Mode: `dev-loop.json` `release_notes`, no key: `cliff`. `cliff`: say the mode is `cliff`, change nothing, stop.
2. Range: from the previous final tag (`vX.Y.Z`, before `<version>`) to `<tag>`, or to `HEAD` while the tag does not exist. No previous final tag: from the first commit.
3. Sources for the range: the commit subjects (`git log --format=%s <from>..<to>`); the PRs they name (`gh pr view <n> --json title,body,closingIssuesReferences`); their issues (`gh issue view <n>`); the specs in `$NOTES/Features/*/Spec.md` whose frontmatter `pr:` is one of these PRs.
4. Write the section in `CHANGELOG.md` (working tree, at the place of its version, newest first). Form:
   - Heading `## [<version>] - <YYYY-MM-DD>`: the version without `v`, the release date (today for a new section).
   - Subsections in this order, each only when it has an item:
     - `### ⬆️ Upgrade`: the manual steps, one numbered list, in the order the user runs them.
     - `### ✨ New Features`: one short paragraph per feature a user can see. A bold lead of a few words, then what it does and why a user cares. At most three sentences.
     - `### 💥 Breaking Changes`: one bullet per change that breaks what worked before.
     - `### 🔒 Security`: one bullet per security feature or fix.
     - `### ⚡️ Performance`: one bullet per speed or cost gain a user can see.
     - `### 📝 Documentation & Content`: one bullet per doc change a user can see.
     - `### 🐛 Bug Fixes`: one bullet per other fix a user can see.
   - One format per subsection: `Upgrade` numbered steps, `New Features` paragraphs, every other subsection bullets. Never mix.
   - Items in each subsection: most important first (most users hit, most harm avoided).
   - Each fact once, in the subsection that fits it best. Never repeat it in another.
   - Last line: `Full changes: https://github.com/<owner>/<repo>/compare/<previous final tag>...<tag>`, `<owner>` and `<repo>` from `git remote get-url origin`. No previous final tag: no line.
   - No commit hash, no commit list. No refactor, test, CI or doc item unless a user sees its effect.
   - An rc section `## [X.Y.Z-rc.N]` holds everything after the last final release and replaces the section of the previous rc of the same `X.Y.Z`. The final `## [X.Y.Z]` replaces the rc section.
5. Do not commit.
6. Published release: when `gh release view <tag>` finds one, run `release-notes --tag <tag> --output <file>`, show the new body, and ask the user to confirm. Only after the user confirms: `gh release edit <tag> --notes-file <file>`.

`--all`: run steps 1 to 5, with steps 2 to 4 for every section of `CHANGELOG.md`, each heading and date kept: `cliff` mode changes nothing, and nothing is committed. Instead of step 6, show every new body of a published release and ask to confirm once for all of them; after the user confirms, `gh release edit` each.

Report: the sections written, the releases edited.
