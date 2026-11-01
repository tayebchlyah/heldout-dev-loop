# Skill suite

One file per subject. A new test file adds its line here.

- `test_dev_loop.py`: entry: loads the files below in chunks.
- `test_devloop.py`, `test_status.py`, `test_merge_check.py`, `test_merge_binding.py`, `test_skill_text.py`, `test_review_text.py`, `test_kit_contracts.py`: `logged`, `gate`, the project config shape, CLI test helpers; `status`, `merge-check`; kit text contracts.
- `test_check_setup.py`, `test_setup_checks.py`, `test_notes_path.py`, `test_mutation.py`: `check_setup.py`; `notes_path.py`; `select_mutants.py`, mutation setup, contract.
- `test_hooks.py`, `test_seal.py`: board hook launch, Claude gate hook; `seal`, `audit`.
- `test_seal_check.py`, `test_search_wall.py`, `test_search_hooks.py`: `seal-check`, `kit-changed`, `weakened`; `search-wall`: direct, via walls.
- `test_size.py`, `test_seal_hooks.py`: `size-check`, seams, `gate` and `seal` size; seal hooks, guard, spec-tester, Codex walls.
- `test_kit_walls.py`, `test_walls.py`, `test_git_guard.py`, `test_push_spellings.py`, `test_gh_guard.py`: path, kit, `cd` walls, canary script, Codex trust warning; deny walls, Codex hooks, git and gh guard.
- `test_board_robust.py`, `test_notes_migrate.py`, `test_notes_migrate_apply.py`: board robustness; `notes-migrate`.
- `test_board_layout.py`, `test_board_tasks.py`, `test_verify.py`, `test_capture.py`: board folders, names; task state, subagents, hook filter; `verify-init`, `drive`, verify checks; `capture`.
- `test_second_review.py`, `test_release.py`, `test_release_record.py`: `second-review`; `release-check`, `release-plan`, `release-notes`; `release-record`.
- `test_release_guard.py`, `test_sandbox_run.py`: release guards, setup, kit, text; `sandbox-run.py` (real Docker; none: skip, `DEVLOOP_REQUIRE_DOCKER=1` fails, set in CI).
- `test_metrics.py`, `test_metrics_cli.py`, `test_ci.py`, `test_ci_skip.py`, `test_kit_reference.py`: `metrics`; `ci wait` (stub `gh`), `ci-skip`; `kit_reference`, `kit_matches`, kit-source flows.
- `test_gardener.py`, `test_retro.py`, `test_feature_gate.py`: `garden` skill, kit text, harness names; `review_due`, `sessions`; `review-merge`, `acceptance`.
- `test_case_floor.py`: case floor.
- `test_upstream_watch.py`, `test_autonomy.py`: upstream watch; `decide`, `pause`, autonomy hooks.
- `test_repo_rules.py`, `test_repo_docs.py`: `repo_rules.py`, gate scope; `AGENTS.md` budget, CLI index, this file index, kit and release docs.
- `test_suite_lock.py`: `scripts/suite_lock.sh`, one suite run at a time across worktrees.
- `junit_report.py`, `board_fixtures.py`: the one JUnit writer; board fixtures the held-out oracle shares.
- `support.py`: shared constants, helpers.
- `fixtures/`: test data. No test file under `skill/`.
