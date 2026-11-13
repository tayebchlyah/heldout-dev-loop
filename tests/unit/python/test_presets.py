"""presets.json: schema, detect order, pinned values and the presets reader."""

from __future__ import annotations

import unittest

from support import SKILL_ROOT

import source_dirs
import stacks

NAMES = ["maven", "gradle", "go", "uv", "poetry", "pip", "pnpm", "yarn", "npm", "cargo", "dotnet", "bundler"]
FAMILIES = {"python", "js", "jvm", "go", "rust", "dotnet", "ruby"}
KEYS = {"name", "family", "detect", "lint", "typecheck", "test", "junit", "source_dirs", "test_dir", "layout", "source"}
UI = ["react", "vue", "svelte", "@angular/core", "solid-js", "next", "nuxt", "astro"]


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _text_or_none(value: object) -> bool:
    return value is None or _text(value)


class PresetsTests(unittest.TestCase):
    def test_names_in_order(self) -> None:
        data = stacks.load_presets()
        self.assertEqual(set(data), {"ui_frameworks", "presets"})
        self.assertEqual([p["name"] for p in data["presets"]], NAMES)
        self.assertEqual(data["ui_frameworks"], UI)

    def test_schema(self) -> None:
        for p in stacks.load_presets()["presets"]:
            with self.subTest(preset=p["name"]):
                self.assertEqual(set(p), KEYS)
                self.assertIn(p["family"], FAMILIES)
                self.assertTrue(isinstance(p["detect"], list) and p["detect"])
                for alternative in p["detect"]:
                    self.assertTrue(isinstance(alternative, list) and alternative)
                    self.assertTrue(all(_text(glob) for glob in alternative))
                for key in ("lint", "typecheck", "test"):
                    self.assertTrue(_text_or_none(p[key]), key)
                self.assertEqual(set(p["junit"]), {"command", "files", "package", "setup"})
                self.assertTrue(_text(p["junit"]["command"]) and _text(p["junit"]["files"]))
                self.assertTrue(_text_or_none(p["junit"]["package"]) and _text_or_none(p["junit"]["setup"]))
                self.assertIsNone(source_dirs.validate(p["source_dirs"]))
                if p["test_dir"] is not None:
                    self.assertIsNone(source_dirs.validate([p["test_dir"]]))
                self.assertEqual(set(p["layout"]), {"init", "dirs"})
                self.assertTrue(_text_or_none(p["layout"]["init"]))
                self.assertTrue(isinstance(p["layout"]["dirs"], list) and all(_text(d) for d in p["layout"]["dirs"]))
                self.assertTrue(isinstance(p["source"], list) and p["source"])
                self.assertTrue(all(isinstance(url, str) and url.startswith("https://") for url in p["source"]))

    def test_pinned_values(self) -> None:
        by = stacks.by_name(stacks.load_presets())
        self.assertEqual(by["go"]["source_dirs"], ["cmd/", "internal/"])
        self.assertIsNone(by["go"]["test_dir"])
        for name in ("npm", "pnpm", "yarn"):
            with self.subTest(preset=name):
                self.assertEqual((by[name]["source_dirs"], by[name]["test_dir"], by[name]["layout"]["dirs"]),
                                 (["src/"], "tests/unit/", ["src/", "tests/unit/"]))
        self.assertEqual(by["dotnet"]["source_dirs"], [])
        self.assertEqual(by["maven"]["junit"]["files"], "target/surefire-reports/TEST-*.xml")
        self.assertEqual(by["cargo"]["junit"]["package"], "cargo-nextest")
        self.assertIn("[profile.ci.junit]", by["cargo"]["junit"]["setup"])
        self.assertIn(".config/nextest.toml", by["cargo"]["junit"]["setup"])
        self.assertIsNone(by["bundler"]["typecheck"])
        self.assertEqual(by["uv"]["detect"], [["pyproject.toml", "uv.lock"]])
        self.assertEqual(by["pip"]["detect"], [["pyproject.toml"], ["requirements.txt"]])

    def test_presets_path_is_beside_the_skill(self) -> None:
        self.assertEqual(stacks.PRESETS, SKILL_ROOT / "presets.json")


if __name__ == "__main__":
    unittest.main()
