#!/usr/bin/env python3
"""Tests for survivor classification in `mutation.run`: message or decision, per file extension."""

from __future__ import annotations

import unittest

import support  # noqa: F401
from mutation.run import classify

ROWS = [
    ("a.py", '-        raise ValueError("bad")\n+        raise ValueError("XXbadXX")', "message"),
    ("a.go", '-\treturn fmt.Errorf("bad")\n+\treturn fmt.Errorf("")', "message"),
    ("a.go", '-\tif s == "x" {\n+\tif s == "" {', "decision"),
    ("a.go", '-\tslog.Info("a")\n+\tslog.Info("b")', "message"),
    ("a.ts", '-  throw new Error("bad")\n+  throw new Error("")', "message"),
    ("a.ts", '-  if (k === "id") {\n+  if (k === "") {', "decision"),
    ("a.rb", '-  raise "bad"\n+  raise ""', "decision"),
    ("a.py", None, "decision"),
    ("a.js", '-  return { message: "a" }\n+  return { message: "b" }', "message"),
    ("a.js", '-  x = 1\n+  x = 2\n   console.log("a")', "decision"),
    ("a.js", ' \n-  console.log("a")\n+  console.log("b")', "message"),
    ("a.js", '-  console.log("a")\n\n+  console.log("b")', "message"),
    ("a.py", '-    raise ValueError("bad window")\n+    raise ValueError("XXbad windowXX")\n', "message"),
    ("a.py", '-    logger.info("fetched %s", name)\n+    logger.info(None, name)\n', "message"),
    ("a.py", '-    return Finding(detail="gap")\n+    return Finding(detail="XXgapXX")\n', "message"),
    ("a.py", '-    return {"status": "ok"}\n+    return {"XXstatusXX": "ok"}\n', "decision"),
    ("a.py", '-    if kind == "gauge":\n+    if kind == "XXgaugeXX":\n', "decision"),
    ("a.py", "-    if count >= limit:\n+    if count > limit:\n", "decision"),
    ("a.py", "-    return total\n+    return None\n", "decision"),
    ("a.py", "-    return math.log(x + 1)\n+    return math.log(x - 1)", "decision"),
    ("a.py", "-    return np.log(x + 1)\n+    return np.log(x - 1)", "decision"),
    ("a.py", '-    _logger.info("a")\n+    _logger.info("b")', "message"),
    ("a.py", '-    LOGGER.warning("a", x + 1)\n+    LOGGER.warning("a", x - 1)', "message"),
    ("a.py", '-    print("a")\n+    print("b")', "message"),
    ("a.go", "-\treturn foo.log.Sum(x + 1)\n+\treturn foo.log.Sum(x - 1)", "decision"),
    ("a.go", "-\treturn mylog.Sum(x + 1)\n+\treturn mylog.Sum(x - 1)", "decision"),
    ("a.py", '-    raise E(404, detail=errors["missing"])\n+    raise E(404, detail=errors["XXmissingXX"])', "decision"),
    ("a.py", '-    assert kind == "gauge", "bad"\n+    assert kind == "XXgaugeXX", "bad"', "decision"),
    ("a.py", '-    assert "id" in row, "bad"\n+    assert "XXidXX" in row, "bad"', "decision"),
    ("a.js", '-console.log("a")\n+console.log("b")\n---x;', "decision"),
]
for ext in ("pyi", "jsx", "tsx", "mjs", "cjs", "vue", "svelte"):
    body = '-    raise ValueError("a")\n+    raise ValueError("b")' if ext == "pyi" else '-  console.log("a")\n+  console.log("b")'
    ROWS.append((f"a.{ext}", body, "message"))


class ClassifyTest(unittest.TestCase):
    def test_classification_table(self) -> None:
        for file, diff, expected in ROWS:
            with self.subTest(file=file, diff=diff):
                self.assertEqual(classify(file, diff), expected)


if __name__ == "__main__":
    unittest.main()
