# review-save

- `review-save --reviewer <name>`: Stage 6 table save. Reads one reviewer table on stdin, checks it with the `review-merge` parser (same table contract), writes the text as given to `<git-dir>/dev-loop/review/<name>.md`, `<git-dir>` the absolute git dir of the current worktree (`rev-parse --absolute-git-dir`, a linked worktree's own), folders made as needed. A header with no data row is valid: 0 findings. `<name>`: a plain file name, `[A-Za-z0-9_][A-Za-z0-9_.:-]*`, no `..`. JSON `file` (the path written), `findings` (row count).
- `review-save --done`: writes the `HEAD` sha and a newline to `<git-dir>/dev-loop/review-done`, the Track S marker `status` and `merge-check` read. JSON `file`, `sha`.
- Exit 1 with `error`, `row` (1 for the first data row, null when no table): the table fails the `review-merge` checks; nothing is written. Exit 2: neither or both of `--reviewer` and `--done`, a bad `<name>`, not in a git worktree, no `HEAD` commit for `--done`.
