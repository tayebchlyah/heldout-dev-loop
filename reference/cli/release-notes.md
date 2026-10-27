# release-notes

- `release-notes --tag <tag> [--repo <path>] [--output <file>]`: the body of the `CHANGELOG.md` section of a tag, the GitHub Release body. It works in both release notes modes and reads none.
- Tag: `v` + `X.Y.Z` or `X.Y.Z-rc.N` (full match; no leading zeros, `N` at least 1). Any other form: exit 2 with `error`.
- Reads `<repo>/CHANGELOG.md` (the working tree, as bytes: a bare carriage return does not split a line). Section: the first line starting `## [` whose version (an optional `v` dropped, up to `]`) equals the tag without its `v`. Body: the lines after the heading up to the next line starting `## ` (two hashes and a space; a `### ` subsection stays in the body), leading and trailing blank lines stripped.
- `--output <file>`: writes the body there, a path relative to the current directory (not to `--repo`), only on success. Without it nothing is written and `output` is null.
- JSON on success, exit 0: `ok` true, `tag`, `version`, `output` (the path as given, or null), `lines` (body line count).
- JSON on failure, exit 1: `ok` false, `tag`, `version`, `reason`: `no-changelog` (no `CHANGELOG.md`), `no-section` (no heading for the version), `empty-section` (only blank lines before the next `## `).
