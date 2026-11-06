"""The seal record `seals.json`: write, update, `seal-dropped`, `seal-record-unreadable`."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from test_seal import SUITE
from test_seal_check import SealRepo

from seal import ACCEPTANCE_DIR, PATCH_TRAILER, SEAL_TRAILER  # noqa: E402
from seal.record import (  # noqa: E402
    add_seal, patch_carriers, read_record, record_branch, record_file, replace_patch, skip_reason)
from test_devloop import git, put, run_cli  # noqa: E402

ENTRY = {"branch": "refs/heads/feat/x", "commit": "c1", "patches": ["p1"], "seal": "s1", "feature": "x", "paths": ["a"]}


def stop(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True)


class RecordTests(SealRepo):
    def conflict_on_main(self) -> None:
        git(self.repo, "checkout", "-q", "main")
        self.commit("README.md", "main side\n")
        git(self.repo, "checkout", "-q", "feat/x")

    def test_add_seal_appends_oldest_first(self) -> None:
        self.assertIsNone(read_record(self.repo))
        add_seal(self.repo, ENTRY)
        add_seal(self.repo, {**ENTRY, "commit": "c2"})
        self.assertEqual([e["commit"] for e in read_record(self.repo) or []], ["c1", "c2"])
        top = git(self.repo, "rev-parse", "--absolute-git-dir").strip()
        self.assertEqual(record_file(self.repo), Path(top) / "dev-loop" / "seals.json")

    def test_replace_patch_appends_the_new_value(self) -> None:
        add_seal(self.repo, ENTRY)
        replace_patch(self.repo, "p1", {**ENTRY, "commit": "c9", "patches": ["p1", "p2"], "seal": "s2", "paths": ["b"]})
        self.assertEqual(read_record(self.repo), [{**ENTRY, "commit": "c9", "patches": ["p1", "p2"], "seal": "s2",
                                                   "paths": ["b"]}])

    def test_replace_patch_without_a_match_adds_an_entry(self) -> None:
        add_seal(self.repo, ENTRY)
        new = {**ENTRY, "commit": "c3", "patches": ["zz", "p3"]}
        replace_patch(self.repo, "zz", new)
        self.assertEqual(read_record(self.repo), [ENTRY, new])
        other = {**ENTRY, "branch": "refs/heads/other", "patches": ["p1", "p4"]}
        replace_patch(self.repo, "p1", other)
        self.assertEqual(read_record(self.repo), [ENTRY, new, other])

    def test_replace_patch_without_a_record_writes_nothing(self) -> None:
        replace_patch(self.repo, "p1", ENTRY)
        self.assertFalse(record_file(self.repo).exists())

    def test_read_record_rejects_other_shapes(self) -> None:
        path = record_file(self.repo)
        path.parent.mkdir(parents=True)
        no_branch = {k: v for k, v in ENTRY.items() if k != "branch"}
        for text in ("not json", "[]", '{"seals": {}}', json.dumps({"seals": [{**ENTRY, "patches": []}]}),
                     json.dumps({"seals": [no_branch]}), json.dumps({"seals": [{**ENTRY, "paths": "a"}]})):
            with self.subTest(text=text):
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    read_record(self.repo)
        path.write_text(json.dumps({"seals": [{**ENTRY, "extra": 1}]}), encoding="utf-8")
        self.assertEqual(read_record(self.repo), [{**ENTRY, "extra": 1}])
        path.unlink()
        path.mkdir()
        with self.assertRaises(ValueError):
            read_record(self.repo)

    def test_add_seal_into_a_folder_raises(self) -> None:
        record_file(self.repo).mkdir(parents=True)
        with self.assertRaises((ValueError, OSError)):
            add_seal(self.repo, ENTRY)

    def test_record_in_a_path_with_a_space(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve() / "ré po"
            repo.mkdir()
            git(repo, "init", "-q")
            add_seal(repo, ENTRY)
            self.assertEqual(read_record(repo), [ENTRY])
            self.assertTrue(str(record_file(repo)).startswith(str(repo)))

    def test_record_branch_and_skip_reason(self) -> None:
        self.commit("README.md", "branch side\n")
        self.assertEqual((record_branch(self.repo), skip_reason(self.repo)), ("refs/heads/feat/x", None))
        git(self.repo, "checkout", "-q", "--detach")
        self.assertEqual((record_branch(self.repo), skip_reason(self.repo)), (None, "detached"))
        git(self.repo, "checkout", "-q", "feat/x")
        self.conflict_on_main()
        stop(self.repo, "rebase", "main")
        self.assertEqual((record_branch(self.repo), skip_reason(self.repo)), ("refs/heads/feat/x", "rebase"))
        git(self.repo, "rebase", "--abort")
        git(self.repo, "checkout", "-q", "-b", "other", "main")
        stop(self.repo, "cherry-pick", "feat/x")
        self.assertEqual(skip_reason(self.repo), "cherry-pick")

    def test_carriers_read_the_last_paragraph_only(self) -> None:
        a = self.commit("a", "a\n", f"a\n\n{PATCH_TRAILER}: v1")
        self.commit("b", "b\n", f"b\n\n{PATCH_TRAILER}: v2\n\nCo-Authored-By: t <t@example.invalid>")
        self.assertEqual(patch_carriers(self.repo), {"v1": a})
        c = self.commit("c", "c\n", f"c\n\n{PATCH_TRAILER}: v1")
        self.assertEqual(patch_carriers(self.repo), {"v1": c})


def trailer(repo: Path, key: str, rev: str = "HEAD") -> str:
    body = git(repo, "log", "-1", "--format=%B", rev)
    return next(line.split(": ", 1)[1].strip() for line in body.splitlines() if line.startswith(f"{key}: "))


class SealWritesTests(SealRepo):
    def test_seal_adds_one_entry_per_seal(self) -> None:
        first = self.seal(SUITE, "a\n")
        entry = {"branch": "refs/heads/feat/x", "commit": first, "patches": [trailer(self.repo, PATCH_TRAILER)],
                 "seal": trailer(self.repo, SEAL_TRAILER), "feature": "x", "paths": [SUITE]}
        self.assertEqual(read_record(self.repo), [entry])
        other = f"{ACCEPTANCE_DIR}/other/test_other.py"
        second = self.seal(other, "o\n")
        record = read_record(self.repo) or []
        self.assertEqual([record[0], record[1]["commit"], record[1]["paths"]], [entry, second, [other]])

    def test_seal_in_a_linked_worktree(self) -> None:
        tree = self.repo.parent / "wt"
        git(self.repo, "worktree", "add", "-q", "-b", "feat/w", str(tree), "main")
        put(tree / SUITE, "w\n")
        git(tree, "add", "--", SUITE)
        code, out = run_cli("seal", "--feature", "x", "--message", "acceptance", cwd=tree)
        self.assertEqual(code, 0, out)
        own = Path(git(tree, "rev-parse", "--absolute-git-dir").strip()) / "dev-loop" / "seals.json"
        self.assertTrue(own.is_file())
        self.assertEqual([e["branch"] for e in read_record(tree) or []], ["refs/heads/feat/w"])
        self.assertIsNone(read_record(self.repo))

    def test_record_folder_fails_seal(self) -> None:
        record_file(self.repo).mkdir(parents=True)
        put(self.repo / SUITE, "a\n")
        git(self.repo, "add", "--", SUITE)
        code, out = run_cli("seal", "--feature", "x", "--message", "acceptance", cwd=self.repo)
        self.assertEqual(code, 1, out)
        self.assertTrue(out["error"].startswith("seal record not written"), out)
        self.assertEqual(out["commit"], git(self.repo, "rev-parse", "HEAD").strip())
        self.assertIn("Acceptance: x", git(self.repo, "log", "-1", "--format=%B"))

    def test_failed_seal_writes_nothing(self) -> None:
        code, out = run_cli("seal", "--feature", "x", "--message", "acceptance", cwd=self.repo)
        self.assertEqual(code, 1, out)
        self.assertFalse(record_file(self.repo).exists())

    def test_detached_seal_writes_no_entry(self) -> None:
        git(self.repo, "checkout", "-q", "--detach")
        self.seal(SUITE, "a\n")
        self.assertIsNone(read_record(self.repo))


TWELVE = "".join(f"line {i}\n" for i in range(12))
OTHER = f"{ACCEPTANCE_DIR}/other/test_other.py"


def seal_conflict(case: SealRepo, paths: tuple[str, ...] = (SUITE,)) -> list[str]:
    git(case.repo, "checkout", "-q", "main")
    for path in paths:
        case.commit(path, TWELVE)
    git(case.repo, "checkout", "-q", "-B", "feat/x", "main")
    old = []
    for path in paths:
        case.seal(path, TWELVE.replace("line 8\n", "line 8\nadded\n"))
        old.append(trailer(case.repo, PATCH_TRAILER))
    git(case.repo, "checkout", "-q", "main")
    for path in paths:
        case.commit(path, TWELVE.replace("line 8\n", "line 8 upstream\n"))
    git(case.repo, "checkout", "-q", "feat/x")
    stop(case.repo, "rebase", "main")
    return old


def resolve(case: SealRepo, path: str = SUITE) -> tuple[int, dict]:
    put(case.repo / path, TWELVE.replace("line 8\n", "line 8 upstream\nadded\n"))
    git(case.repo, "add", "--", path)
    return run_cli("seal", "--rebase-continue", cwd=case.repo)


class RebaseContinueTests(SealRepo):
    def test_rebase_continue_appends_the_new_value(self) -> None:
        [old] = seal_conflict(self)
        code, out = resolve(self)
        self.assertEqual((code, out.get("rebase")), (0, "continued"), out)
        [entry] = read_record(self.repo) or []
        new = trailer(self.repo, PATCH_TRAILER)
        self.assertNotEqual(old, new)
        self.assertEqual((entry["patches"], entry["commit"], entry["seal"]),
                         ([old, new], git(self.repo, "rev-parse", "HEAD").strip(), out["seal"]))

    def test_no_record_stays_no_record(self) -> None:
        git(self.repo, "checkout", "-q", "main")
        self.commit(SUITE, TWELVE)
        git(self.repo, "checkout", "-q", "-B", "feat/x", "main")
        self.seal(SUITE, TWELVE.replace("line 8\n", "line 8\nadded\n"))
        record_file(self.repo).unlink()
        git(self.repo, "checkout", "-q", "main")
        self.commit(SUITE, TWELVE.replace("line 8\n", "line 8 upstream\n"))
        git(self.repo, "checkout", "-q", "feat/x")
        stop(self.repo, "rebase", "main")
        self.assertEqual(resolve(self)[1].get("rebase"), "continued")
        self.assertFalse(record_file(self.repo).exists())

    def test_no_matching_entry_adds_one(self) -> None:
        [old] = seal_conflict(self)
        record_file(self.repo).write_text(json.dumps({"seals": [{**ENTRY, "branch": "refs/heads/other"}]}),
                                          encoding="utf-8")
        self.assertEqual(resolve(self)[0], 0)
        record = read_record(self.repo) or []
        self.assertEqual(len(record), 2)
        self.assertEqual((record[1]["branch"], record[1]["patches"], record[1]["feature"]),
                         ("refs/heads/feat/x", [old, trailer(self.repo, PATCH_TRAILER)], "x"))

    def test_two_stops_update_two_entries(self) -> None:
        old = seal_conflict(self, (SUITE, OTHER))
        self.assertEqual(resolve(self, SUITE)[1].get("rebase"), "stopped")
        self.assertEqual(resolve(self, OTHER)[1].get("rebase"), "continued")
        record = read_record(self.repo) or []
        heads = [git(self.repo, "rev-parse", rev).strip() for rev in ("HEAD~1", "HEAD")]
        news = [trailer(self.repo, PATCH_TRAILER, rev) for rev in ("HEAD~1", "HEAD")]
        self.assertEqual([(e["patches"], e["commit"]) for e in record],
                         [([old[0], news[0]], heads[0]), ([old[1], news[1]], heads[1])])

    def test_failed_run_changes_nothing(self) -> None:
        seal_conflict(self)
        before = record_file(self.repo).read_bytes()
        put(self.repo / SUITE, TWELVE.replace("line 8\n", "line 8 upstream\nadded\n"))
        put(self.repo / "README.md", "outside\n")
        git(self.repo, "add", "-A")
        code, out = run_cli("seal", "--rebase-continue", cwd=self.repo)
        self.assertEqual(code, 1, out)
        self.assertEqual(record_file(self.repo).read_bytes(), before)
