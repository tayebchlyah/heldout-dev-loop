"""The `security-audit` skill text a brief copies: sections and fenced blocks by heading, D8 block refs, and the verdict
branches of `report-schema.json`. A miss is `skill-changed` with the file and section."""

from __future__ import annotations

import json
from pathlib import Path
from typing import NoReturn

from audit import common


class SkillChanged(Exception):
    """The skill folder lacks a file, heading, fence or schema branch a step reads."""

    def __init__(self, file: str, section: str) -> None:
        super().__init__(f"{file}: {section}")
        self.file = file
        self.section = section


def changed(exc: SkillChanged) -> NoReturn:
    """Stop the step with `skill-changed`."""
    common.fail("skill-changed", file=exc.file, section=exc.section)


def lines(skill: Path, file: str, section: str) -> list[str]:
    try:
        return (skill / file).read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, ValueError):
        raise SkillChanged(file, section) from None


def headings(text: list[str]) -> list[int]:
    found, fenced = [], False
    for index, line in enumerate(text):
        if line.strip().startswith("```"):
            fenced = not fenced
        elif not fenced and line.startswith("#"):
            found.append(index)
    return found


def level(line: str) -> int:
    return len(line) - len(line.lstrip("#"))


def matches(line: str, heading: str) -> bool:
    rest = line.rstrip("\n")[len(heading):]
    return line.startswith(heading) and not (rest[:1].isalnum())


def section_lines(skill: Path, file: str, heading: str) -> list[str]:
    text = lines(skill, file, heading)
    marks = headings(text)
    for n, index in enumerate(marks):
        if matches(text[index], heading):
            end = next((m for m in marks[n + 1:] if level(text[m]) <= level(text[index])), len(text))
            return text[index:end]
    raise SkillChanged(file, heading)


def section(skill: Path, file: str, heading: str) -> str:
    """The heading line through the end of its section (the next heading of the same or a higher level)."""
    return "".join(section_lines(skill, file, heading))


def fences(skill: Path, file: str, heading: str, count: int) -> list[str]:
    """The first `count` fenced blocks of the section, each with its fence lines."""
    blocks, current = [], None
    for line in section_lines(skill, file, heading):
        if line.strip().startswith("```"):
            if current is None:
                current = [line]
            else:
                blocks.append("".join(current + [line]))
                current = None
        elif current is not None:
            current.append(line)
    if len(blocks) < count:
        raise SkillChanged(file, heading)
    return blocks[:count]


def span(skill: Path, file: str, start: str, stop: str) -> str:
    """From the first heading starting with `start` up to the first later heading starting with `stop`."""
    text = lines(skill, file, start)
    marks = headings(text)
    first = next((i for i in marks if matches(text[i], start)), None)
    end = next((i for i in marks if first is not None and i > first and matches(text[i], stop)), None)
    if first is None or end is None:
        raise SkillChanged(file, start)
    return "".join(text[first:end])


def block(skill: Path, ref: str) -> str:
    """A block ref `FILE.md#Name`: the `## Name` (or `## Name (...`) section, else the `**Name**` paragraph group."""
    file, _, name = ref.partition("#")
    text = lines(skill, file, name)
    marks = headings(text)
    for index in marks:
        line = text[index].rstrip("\n")
        if line == f"## {name}" or line.startswith(f"## {name} ("):
            end = next((m for m in marks if m > index and level(text[m]) <= 2), len(text))
            return "".join(text[index:end])
    fenced = False
    for index, line in enumerate(text):
        if line.strip().startswith("```"):
            fenced = not fenced
        elif not fenced and line.startswith(f"**{name}**"):
            end = next((m for m in range(index + 1, len(text))
                        if text[m].startswith("**") or m in marks), len(text))
            return "".join(text[index:end])
    raise SkillChanged(file, name)


def schema_branch(skill: Path, verdict: str) -> str:
    """The `report-schema.json` branch of a verdict, as indented JSON."""
    try:
        schema = json.loads((skill / "report-schema.json").read_text(encoding="utf-8"))
        branches = schema["items"]["oneOf"]
    except (OSError, ValueError, KeyError, TypeError):
        raise SkillChanged("report-schema.json", verdict) from None
    for branch in branches if isinstance(branches, list) else []:
        if isinstance(branch, dict) and (branch.get("properties") or {}).get("verdict", {}).get("const") == verdict:
            return json.dumps(branch, indent=2, ensure_ascii=False)
    raise SkillChanged("report-schema.json", verdict)
