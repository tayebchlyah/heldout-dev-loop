# ci-skip

- `ci-skip --base <rev> [--head <rev>] [--repo <path>] [--github-output]`: may CI skip this diff: only a release (version files, `CHANGELOG.md`) or a kit upgrade. `--head` default `HEAD`, `--repo` default `.`.
- Changed paths: `git diff --name-only --no-renames <base>...<head>`. Every content comparison uses the merge base of `<base>` and `<head>` (`mb`), so a `main` change after the branch point never counts as a change of the PR. No merge base: every path blocks.
- A changed path is allowed when one rule holds:
  - (a) `CHANGELOG.md`.
  - (b) a path of `version_files` (`dev-loop.json` at `<head>`; absent or `[]`: none) whose diff from `mb` is version-only: every hunk removes as many lines as it adds, line i removed and line i added are equal after each replaces its version tokens (`(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-rc\.[1-9]\d*)?`) by one placeholder. The changed token pairs of all these files are exactly one pair (old, new), new is the version of the first `## [<version>]` heading (a leading `v` dropped) of `CHANGELOG.md` at `<head>`, and `CHANGELOG.md` changed. A file that fails blocks itself; a pair, heading or `CHANGELOG.md` mismatch blocks every (b) file.
  - (c) `dev-loop.json`, when it is a JSON object at `mb` and at `<head>` and the two are equal without their `version` key.
  - (d) only when the `.dev-loop/MANIFEST.json` `version` at `mb` and at `<head>` are both set and differ: `.dev-loop/MANIFEST.json`; a path under a kit root of `release.py` that is a key of `files` in the `<head>` MANIFEST and matches it in the tree of `<head>` (`sha256:<hex>`, a symlink `link:<target>`); a path deleted at `<head>`, under a kit root, a key in the `mb` MANIFEST.
  - Never: `.claude/settings.json`, `.codex/hooks.json`, `.codex/config.toml`.
- JSON `skip` (at least one changed path and none blocking), `kind` (`release` when `CHANGELOG.md` changed, else `kit-upgrade`; null when `skip` is false), `blocking` (sorted paths no rule allows), `base`, `head` (full shas).
- `--github-output`: also appends `skip=true` or `skip=false` and a newline to the file `GITHUB_OUTPUT` names.
- Exit 0 when the check ran (`skip` true or false). Exit 1 with `error`: not a git repo, a rev that does not resolve to a commit (the all-zero sha of a new branch push too), `version_files` not a list of non-empty strings with no leading `/` and no `..` segment. Exit 2: usage, `--github-output` with `GITHUB_OUTPUT` unset.
- CI runs the `ci-skip` of the base kit, never the kit of the PR (`reference/kit.md`).
