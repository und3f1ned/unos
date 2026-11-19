"""The architecture decision records in docs/adr keep their format (ADR-0001).

Each check below is a function of a repository root that returns its problems, one line
each, naming the file and, where it helps, the line. The unit tests run every check on
small fixture trees in tmp_path, with good and bad cases; the tests at the end run them
on this repository.

* Names and numbering: docs/adr holds README.md, template.md and NNNN-slug.md files,
  numbered from 0001 without gaps or duplicates.
* Front matter: status, date, area and decided-by with their allowed values, supersedes
  (an optional list) and superseded-by (exactly when the status is superseded).
* The title line `# ADR-NNNN: <title>` with the file's number, in sentence case, and the
  sections Context, Decision, Consequences and, optionally, Considered options.
* Supersession is mutual, names existing ADRs and goes forward in time, and a superseded
  ADR names its successor at the top of Context or Consequences.
* docs/adr/README.md lists every ADR exactly once, in the table of its area, with its
  title, current status and date.
* ADRs are self-contained and in English: no paths of a session's scratch folder, no
  labels of the scratch documents they were distilled from, no Cyrillic.
* Every ADR-NNNN in the code and the docs, and every link to an ADR file in the docs,
  names an existing ADR.
* The code and its tests cite ADRs, not the scratch design documents of slice 2 or the
  ids of review findings.

This file names the scratch patterns and builds bad examples, so the scans skip it.
"""

from __future__ import annotations

import datetime
import json
import os
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ADR_DIR = "docs/adr"
SELF = "supervisor/tests/test_adrs.py"

# Area of an ADR -> heading of its section in docs/adr/README.md, in the README's order.
AREAS = {
    "process": "Process",
    "architecture": "Architecture",
    "security": "Security",
    "auth": "Auth",
    "storage": "Storage",
    "shares": "Shares",
    "apps": "Apps",
    "backup": "Backup",
    "files": "Files",
    "system": "System",
    "webui": "Web UI",
    "deploy": "Deploy",
    "device": "Device",
    "testing": "Testing",
    "ci": "CI",
}
STATUSES = ("proposed", "accepted", "superseded")
DECIDERS = ("owner", "agent")
REQUIRED_KEYS = ("status", "date", "area", "decided-by")
OPTIONAL_KEYS = ("supersedes", "superseded-by")
SECTIONS = ("Context", "Decision", "Consequences")
OPTIONAL_LAST_SECTION = "Considered options"

ADR_FILE = re.compile(r"(\d{4})-([a-z0-9]+(?:-[a-z0-9]+)*)\.md")
ADR_ID = re.compile(r"ADR-(\d{4})")
TITLE_LINE = re.compile(r"# ADR-(\d{4}): (.*)")
FRONT_MATTER_LINE = re.compile(r"([a-z][a-z-]*): (\S.*)")
ID_LIST = re.compile(r"\[(.*)\]")

README_HEADER = re.compile(r"\|\s*ADR\s*\|\s*Title\s*\|\s*Status\s*\|\s*Date\s*\|")
README_SEPARATOR = re.compile(r"\|(\s*:?-{3,}:?\s*\|){4}")
README_ROW = re.compile(r"\|\s*\[(\d{4})\]\(([^)\s]*)\)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|")

# Mentions of ADRs anywhere: an id, a path ending in adr/NNNN-slug.md, or (inside
# docs/adr) a relative link to NNNN-slug.md.
ADR_MENTION = re.compile(r"\bADR-(\d+)\b")
ADR_PATH = re.compile(r"\badr/(\d{4}-[a-z0-9-]+\.md)\b")
ADR_RELATIVE_LINK = re.compile(r"\]\((\d{4}-[a-z0-9-]+\.md)(?:#[^)]*)?\)")

# How the slice 2 code once cited its scratch design documents (the spec's sections,
# the decisions-v2 notes, the numbered decisions of a review round) and the findings
# of its reviews (R-F17, S-F3). Code cites the ADR of a rule instead, or states the rule.
CODE_SCRATCH_REFERENCE = re.compile(
    r"\bspec(-v2)? \d+(\.\d+)*|decisions-v2|spec-v2|\bdecision \d+ of|\bD\d+ of decisions|\b[RS]-F\d+\b",
    re.IGNORECASE,
)
# What an ADR must not contain either: an ADR is read without the session it came from.
ADR_SCRATCH_REFERENCES = (
    (CODE_SCRATCH_REFERENCE, "a section or label of a scratch design or review document"),
    (re.compile(r"/private/tmp|/tmp/claude-|scratchpad", re.IGNORECASE), "a path in a session's scratch folder"),
    (re.compile(r"\bdecisions? \d+\b", re.IGNORECASE), "a numbered decision of a scratch document"),
    (re.compile(r"\bround \d+\b", re.IGNORECASE), "a review round"),
    (re.compile(r"\b[CD]\d{1,2}\b"), "a label of a scratch decision list"),
    (re.compile(r"\bI\d{1,2}\b"), "an invariant label of a scratch design"),
    (re.compile(r"\b(?:BRIEF|deltas-[a-z]+|adr-plan)\.(?:md|json)\b"), "a scratch document"),
)
CYRILLIC = re.compile(r"[Ѐ-ӿ]")

# Where an ADR id or a link to an ADR file must point to an existing ADR. The first
# line is the list of ADR-0001's checks; the others hold ADR ids too.
MENTION_ROOTS = (
    "supervisor", "webui/src", "webui/tests", "deploy", "docs", "scripts", ".agents",
    "AGENTS.md", "CONTRIBUTING.md", "GLOSSARY.md", "PROGRESS.md", "README.md", "feature-list.json",
    "CLAUDE.md", "CHANGELOG.md", "Makefile", "webui/AGENTS.md", "webui/playwright.config.ts", ".claude", ".github",
    "dev.env.example",
)
CODE_ROOTS = ("supervisor/src", "supervisor/tests", "webui/src", "webui/tests")
# Below a scan root, directories whose name starts with '.' are skipped as well
# (supervisor/.venv, caches); files named *.env hold secrets and are never read.
SKIPPED_DIRS = {"node_modules", "__pycache__", "venv", "build", "test-results", "playwright-report"}
SCANNED_EXCLUDED = {SELF}


# --- reading ADRs ------------------------------------------------------------------


@dataclass
class Adr:
    """One docs/adr/NNNN-slug.md file, parsed only as far as it parses."""

    rel: str
    name: str
    number: int
    lines: list[str]
    meta: dict[str, str] = field(default_factory=dict)
    meta_lines: dict[str, int] = field(default_factory=dict)
    front_matter_problems: list[str] = field(default_factory=list)
    body_start: int = 0  # index of the first line after the front matter

    def ids(self, key: str) -> list[int]:
        """The ADR numbers of supersedes or superseded-by, ignoring malformed items."""
        value = self.meta.get(key, "")
        listed = ID_LIST.fullmatch(value)
        items = listed.group(1).split(",") if listed else [value]
        return [int(m.group(1)) for item in items if (m := ADR_ID.fullmatch(item.strip()))]

    def title_line(self) -> tuple[int, str] | None:
        """(line number, text) of the first non-blank line after the front matter."""
        for index in range(self.body_start, len(self.lines)):
            if self.lines[index].strip():
                return index + 1, self.lines[index]
        return None

    def title(self) -> str | None:
        found = self.title_line()
        match = TITLE_LINE.fullmatch(found[1]) if found else None
        return match.group(2).strip() if match else None

    def sections(self) -> list[tuple[str, int, list[str]]]:
        """(heading, line number, lines) of each '## ' section, outside code fences."""
        found: list[tuple[str, int, list[str]]] = []
        fenced = False
        for number, line in enumerate(self.lines[self.body_start:], self.body_start + 1):
            if line.startswith(("```", "~~~")):
                fenced = not fenced
            if not fenced and line.startswith("## "):
                found.append((line[3:].strip(), number, []))
            elif found:
                found[-1][2].append(line)
        return found


def parse_front_matter(adr: Adr) -> None:
    lines = adr.lines
    if not lines or lines[0] != "---":
        adr.front_matter_problems.append("line 1: an ADR starts with its front matter ('---')")
        return
    end = next((index for index in range(1, len(lines)) if lines[index] == "---"), None)
    if end is None:
        adr.front_matter_problems.append("the front matter has no closing '---'")
        return
    adr.body_start = end + 1
    for index in range(1, end):
        match = FRONT_MATTER_LINE.fullmatch(lines[index].rstrip())
        if not match:
            adr.front_matter_problems.append(f"line {index + 1}: not 'key: value': {lines[index]!r}")
            continue
        key, value = match.groups()
        if key in adr.meta:
            adr.front_matter_problems.append(f"line {index + 1}: {key} is given twice")
        adr.meta[key] = value
        adr.meta_lines[key] = index + 1


def adr_dir(root: Path) -> Path:
    return root / ADR_DIR


def load_adrs(root: Path) -> list[Adr]:
    """Every NNNN-slug.md of docs/adr in name order (duplicate numbers included)."""
    directory = adr_dir(root)
    if not directory.is_dir():
        return []
    adrs = []
    for path in sorted(directory.iterdir()):
        match = ADR_FILE.fullmatch(path.name)
        if match and path.is_file():
            adr = Adr(f"{ADR_DIR}/{path.name}", path.name, int(match.group(1)), path.read_text(encoding="utf-8").splitlines())
            parse_front_matter(adr)
            adrs.append(adr)
    return adrs


def by_number(adrs: list[Adr]) -> dict[int, Adr]:
    """The first ADR of each number (check_names reports duplicates)."""
    found: dict[int, Adr] = {}
    for adr in adrs:
        found.setdefault(adr.number, adr)
    return found


def adr_id(number: int) -> str:
    return f"ADR-{number:04d}"


def parse_date(value: str | None) -> datetime.date | None:
    if value is None or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


def first_paragraph(lines: list[str]) -> str:
    paragraph: list[str] = []
    for line in lines:
        if line.strip():
            paragraph.append(line)
        elif paragraph:
            break
    return " ".join(paragraph)


def sentence_case_problem(title: str) -> str | None:
    """Why a title is not one sentence in sentence case, or None."""
    if not title:
        return "the title is empty"
    if title[0].islower():
        return "the title starts with a lowercase letter"
    if title.endswith("."):
        return "the title ends with a period"
    if "|" in title:
        return "the title contains '|', which breaks the README's table"
    words = [word.strip(",.:;()'\"`") for word in title.split()[1:]]
    plain = [word for word in words if re.fullmatch(r"[A-Za-z][a-z]+", word)]
    if len(plain) >= 3 and 2 * sum(word[0].isupper() for word in plain) > len(plain):
        return "the title is in Title Case; write it in sentence case"
    return None


# --- the checks ----------------------------------------------------------------------


def check_names(root: Path) -> list[str]:
    """docs/adr holds README.md, template.md and NNNN-slug.md, numbered 0001, 0002, ..."""
    directory = adr_dir(root)
    if not directory.is_dir():
        return [f"{ADR_DIR} is missing"]
    problems = [f"{ADR_DIR}/{name} is missing" for name in ("README.md", "template.md") if not (directory / name).is_file()]
    numbers: dict[int, list[str]] = {}
    for path in sorted(directory.iterdir()):
        if path.name.startswith(".") or path.name in ("README.md", "template.md"):
            continue
        match = ADR_FILE.fullmatch(path.name)
        if not match or not path.is_file():
            problems.append(f"{ADR_DIR}/{path.name}: not an ADR file name (NNNN-slug.md: four digits, then lowercase words and hyphens)")
            continue
        numbers.setdefault(int(match.group(1)), []).append(path.name)
    for number, names in sorted(numbers.items()):
        if len(names) > 1:
            problems.append(f"{ADR_DIR}: {adr_id(number)} is taken by {len(names)} files: {', '.join(names)}")
    if not numbers:
        problems.append(f"{ADR_DIR} has no ADR")
    elif 0 in numbers:
        problems.append(f"{ADR_DIR}: numbering starts at 0001, not 0000")
    missing = sorted(set(range(1, max(numbers, default=0) + 1)) - set(numbers))
    if missing:
        problems.append(f"{ADR_DIR}: numbers are contiguous from 0001; missing: {', '.join(f'{n:04d}' for n in missing)}")
    return problems


def check_front_matter(root: Path) -> list[str]:
    """status, date, area and decided-by are valid; supersedes and superseded-by are well-formed."""
    problems = []
    for adr in load_adrs(root):
        found = [f"{adr.rel}: {problem}" for problem in adr.front_matter_problems]
        if not adr.front_matter_problems or adr.meta:
            meta = adr.meta

            def at(key: str) -> str:
                return f"{adr.rel}:{adr.meta_lines[key]}" if key in adr.meta_lines else adr.rel  # noqa: B023 - called in this iteration

            for key in REQUIRED_KEYS:
                if key not in meta:
                    found.append(f"{adr.rel}: the front matter has no {key}")
            for key in meta:
                if key not in REQUIRED_KEYS + OPTIONAL_KEYS:
                    found.append(f"{at(key)}: unknown key {key} (keys: {', '.join(REQUIRED_KEYS + OPTIONAL_KEYS)})")
            if "status" in meta and meta["status"] not in STATUSES:
                found.append(f"{at('status')}: status {meta['status']!r} is not one of {', '.join(STATUSES)}")
            if "date" in meta and parse_date(meta["date"]) is None:
                found.append(f"{at('date')}: date {meta['date']!r} is not a date YYYY-MM-DD")
            if "area" in meta and meta["area"] not in AREAS:
                found.append(f"{at('area')}: area {meta['area']!r} is not one of {', '.join(AREAS)}")
            if "decided-by" in meta and meta["decided-by"] not in DECIDERS:
                found.append(f"{at('decided-by')}: decided-by {meta['decided-by']!r} is not one of {', '.join(DECIDERS)}")
            if "supersedes" in meta:
                listed = ID_LIST.fullmatch(meta["supersedes"])
                items = [item.strip() for item in listed.group(1).split(",")] if listed and listed.group(1).strip() else []
                if not listed or not all(ADR_ID.fullmatch(item) for item in items):
                    found.append(f"{at('supersedes')}: supersedes is a list of ADR ids, like [ADR-0012, ADR-0013]")
                elif len(set(items)) < len(items):
                    found.append(f"{at('supersedes')}: supersedes lists an ADR twice")
            if "superseded-by" in meta and not ADR_ID.fullmatch(meta["superseded-by"]):
                found.append(f"{at('superseded-by')}: superseded-by is one ADR id, like ADR-0042")
            if meta.get("status") == "superseded" and "superseded-by" not in meta:
                found.append(f"{adr.rel}: a superseded ADR names its successor in superseded-by")
            if "superseded-by" in meta and meta.get("status") != "superseded":
                found.append(f"{at('superseded-by')}: superseded-by goes with status superseded")
        problems += found
    return problems


def check_titles_and_sections(root: Path) -> list[str]:
    """`# ADR-NNNN: Title` with the file's number, then Context, Decision, Consequences."""
    problems = []
    for adr in load_adrs(root):
        found = adr.title_line()
        match = TITLE_LINE.fullmatch(found[1]) if found else None
        if not match:
            where = f"{adr.rel}:{found[0]}" if found else adr.rel
            problems.append(f"{where}: the first line after the front matter is the title '# {adr_id(adr.number)}: <title>'")
            continue
        if int(match.group(1)) != adr.number:
            problems.append(f"{adr.rel}:{found[0]}: the title says ADR-{match.group(1)}; the file is {adr_id(adr.number)}")
        reason = sentence_case_problem(match.group(2).strip())
        if reason:
            problems.append(f"{adr.rel}:{found[0]}: {reason}")
        sections = adr.sections()
        headings = [heading for heading, _, _ in sections]
        if headings not in (list(SECTIONS), [*SECTIONS, OPTIONAL_LAST_SECTION]):
            problems.append(
                f"{adr.rel}: the sections are {', '.join(headings) or 'missing'}; "
                f"expected {', '.join(SECTIONS)} and optionally {OPTIONAL_LAST_SECTION}"
            )
        for heading, number, lines in sections:
            if not any(line.strip() for line in lines):
                problems.append(f"{adr.rel}:{number}: the section {heading} is empty")
        fenced = False
        for number, line in enumerate(adr.lines[found[0]:], found[0] + 1):
            if line.startswith(("```", "~~~")):
                fenced = not fenced
            elif not fenced and line.startswith("# "):
                problems.append(f"{adr.rel}:{number}: a second title; sections are '## ' headings")
    return problems


def check_supersession(root: Path) -> list[str]:
    """A superseded-by B exactly when B supersedes A; both exist; B is not older than A."""
    adrs = load_adrs(root)
    numbers = by_number(adrs)
    problems = []
    for adr in adrs:
        for old in adr.ids("supersedes"):
            predecessor = numbers.get(old)
            if old == adr.number:
                problems.append(f"{adr.rel}: an ADR does not supersede itself")
            elif predecessor is None:
                problems.append(f"{adr.rel}: supersedes {adr_id(old)}, which does not exist")
            else:
                if predecessor.ids("superseded-by") != [adr.number] or predecessor.meta.get("status") != "superseded":
                    problems.append(
                        f"{adr.rel}: supersedes {adr_id(old)}, so {predecessor.rel} has status superseded "
                        f"and superseded-by: {adr_id(adr.number)}"
                    )
                new_date, old_date = parse_date(adr.meta.get("date")), parse_date(predecessor.meta.get("date"))
                if new_date and old_date and new_date < old_date:
                    problems.append(f"{adr.rel}: dated {new_date}, before {adr_id(old)} ({old_date}) that it supersedes")
        for new in adr.ids("superseded-by"):
            successor = numbers.get(new)
            if new == adr.number:
                problems.append(f"{adr.rel}: an ADR is not superseded by itself")
                continue
            if successor is None:
                problems.append(f"{adr.rel}: superseded by {adr_id(new)}, which does not exist")
                continue
            if adr.number not in successor.ids("supersedes"):
                problems.append(f"{adr.rel}: superseded by {adr_id(new)}, so {successor.rel} lists {adr_id(adr.number)} in supersedes")
            tops = {heading: first_paragraph(lines) for heading, _, lines in adr.sections()}
            if not any(adr_id(new) in tops.get(heading, "") for heading in ("Context", "Consequences")):
                problems.append(f"{adr.rel}: a superseded ADR names {adr_id(new)} at the top of its Context or Consequences")
    return problems


def readme_status(adr: Adr, numbers: dict[int, Adr]) -> str | None:
    """The README's status cell of an ADR, or None when its successor is unknown."""
    if adr.meta.get("status") != "superseded":
        return adr.meta.get("status")
    successors = adr.ids("superseded-by")
    successor = numbers.get(successors[0]) if successors else None
    return f"superseded by [{successor.number:04d}]({successor.name})" if successor else None


def readme_row(adr: Adr, numbers: dict[int, Adr]) -> str:
    status = readme_status(adr, numbers) or "<status>"
    return f"| [{adr.number:04d}]({adr.name}) | {adr.title() or '<title>'} | {status} | {adr.meta.get('date', '<date>')} |"


def check_readme(root: Path) -> list[str]:
    """README.md lists every ADR once, in its area's table, with title, status and date."""
    readme = adr_dir(root) / "README.md"
    rel = f"{ADR_DIR}/README.md"
    if not readme.is_file():
        return [f"{rel} is missing"]
    adrs = load_adrs(root)
    numbers = by_number(adrs)
    headings = {heading: area for area, heading in AREAS.items()}
    order = list(AREAS)
    problems: list[str] = []
    listed: dict[int, int] = {}
    area: str | None = None
    previous_area_index, previous_number = -1, 0
    for line_number, line in enumerate(readme.read_text(encoding="utf-8").splitlines(), 1):
        where = f"{rel}:{line_number}"
        if line.startswith("## "):
            heading = line[3:].strip()
            area = headings.get(heading)
            previous_number = 0
            if area is None:
                problems.append(f"{where}: '{heading}' is not an area heading ({', '.join(AREAS.values())})")
            elif order.index(area) <= previous_area_index:
                problems.append(f"{where}: the section {heading} is out of order or repeated (order: {', '.join(AREAS.values())})")
            else:
                previous_area_index = order.index(area)
            continue
        stripped = line.strip()
        if not stripped.startswith("|") or README_HEADER.fullmatch(stripped) or README_SEPARATOR.fullmatch(stripped):
            continue
        row = README_ROW.fullmatch(stripped)
        if not row:
            problems.append(f"{where}: not a row '| [NNNN](NNNN-slug.md) | Title | Status | Date |'")
            continue
        number, target, title, status, date = int(row.group(1)), row.group(2), row.group(3), row.group(4), row.group(5)
        adr = numbers.get(number)
        if adr is None:
            problems.append(f"{where}: lists {adr_id(number)}, which does not exist")
            continue
        if number in listed:
            problems.append(f"{where}: lists {adr_id(number)} again (first on line {listed[number]})")
        listed.setdefault(number, line_number)
        if target != adr.name:
            problems.append(f"{where}: the link of {adr_id(number)} is {target}; the file is {adr.name}")
        if area is not None and adr.meta.get("area") in AREAS and adr.meta["area"] != area:
            problems.append(f"{where}: {adr_id(number)} belongs under '## {AREAS[adr.meta['area']]}'")
        if area is None:
            problems.append(f"{where}: {adr_id(number)} is listed outside an area section")
        if number <= previous_number:
            problems.append(f"{where}: rows are in number order; {adr_id(number)} comes after {adr_id(previous_number)}")
        previous_number = max(previous_number, number)
        if adr.title() is not None and title != adr.title():
            problems.append(f"{where}: the title of {adr_id(number)} is {adr.title()!r}, not {title!r}")
        expected = readme_status(adr, numbers)
        if expected is not None and status != expected:
            problems.append(f"{where}: the status of {adr_id(number)} is {expected!r}, not {status!r}")
        if "date" in adr.meta and date != adr.meta["date"]:
            problems.append(f"{where}: the date of {adr_id(number)} is {adr.meta['date']}, not {date}")
    for adr in adrs:
        if adr.number not in listed and numbers.get(adr.number) is adr:
            section = AREAS.get(adr.meta.get("area", ""), "<area>")
            problems.append(f"{rel}: no row for {adr_id(adr.number)}; add under '## {section}': {readme_row(adr, numbers)}")
    return problems


def check_self_contained(root: Path) -> list[str]:
    """No ADR, nor the README or the template, cites a scratch path or document, or has Cyrillic."""
    directory = adr_dir(root)
    if not directory.is_dir():
        return []
    problems = []
    for path in sorted(directory.glob("*.md")):
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            where = f"{ADR_DIR}/{path.name}:{line_number}"
            for pattern, what in ADR_SCRATCH_REFERENCES:
                for match in pattern.finditer(line):
                    problems.append(f"{where}: {match.group(0)!r} is {what}; an ADR cites repo files, tests and ADRs")
            if CYRILLIC.search(line):
                problems.append(f"{where}: Cyrillic text; repository text is English (ADR-0200)")
    return problems


def text_files(root: Path, rel_roots: tuple[str, ...]) -> Iterator[tuple[str, list[str]]]:
    """(path relative to root, lines) of the text files below each existing root."""
    for rel_root in rel_roots:
        start = root / rel_root
        if start.is_file():
            paths = [start]
        elif start.is_dir():
            paths = []
            for directory, dirnames, filenames in os.walk(start):
                dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIPPED_DIRS)
                paths += [Path(directory) / name for name in sorted(filenames)]
        else:
            continue
        for path in paths:
            rel = path.relative_to(root).as_posix()
            if rel in SCANNED_EXCLUDED or path.name.endswith(".env") or not path.is_file():
                continue
            data = path.read_bytes()
            if b"\0" in data:
                continue
            # Lines as grep counts them: str.splitlines() would also split at U+2028
            # and other separators that strings in the code may hold.
            yield rel, data.decode("utf-8", errors="replace").split("\n")


def check_adr_mentions(root: Path) -> list[str]:
    """Every ADR-NNNN and every link to docs/adr/NNNN-slug.md names an existing ADR."""
    directory = adr_dir(root)
    names = {path.name for path in directory.iterdir()} if directory.is_dir() else set()
    numbers = {int(m.group(1)) for name in names if (m := ADR_FILE.fullmatch(name))}
    problems = []
    for rel, lines in text_files(root, MENTION_ROOTS):
        inside = rel.startswith(f"{ADR_DIR}/")
        # Docs link to ADR files; code cites ADR ids, and a test may use such a path as data.
        markdown = rel.endswith(".md")
        for line_number, line in enumerate(lines, 1):
            where = f"{rel}:{line_number}"
            for match in ADR_MENTION.finditer(line):
                digits = match.group(1)
                if len(digits) != 4:
                    problems.append(f"{where}: {match.group(0)} is not an ADR id (four digits: ADR-NNNN)")
                elif int(digits) not in numbers:
                    problems.append(f"{where}: {match.group(0)} does not exist in {ADR_DIR}")
            links = [m.group(1) for m in ADR_PATH.finditer(line)] if markdown else []
            if inside:
                links += [m.group(1) for m in ADR_RELATIVE_LINK.finditer(line)]
            for name in links:
                if name not in names:
                    problems.append(f"{where}: {name} is not a file of {ADR_DIR}")
    return problems


def check_code_cites_adrs(root: Path) -> list[str]:
    """No comment, docstring or test of the code cites the scratch design of slice 2."""
    problems = []
    for rel, lines in text_files(root, CODE_ROOTS):
        for line_number, line in enumerate(lines, 1):
            for match in CODE_SCRATCH_REFERENCE.finditer(line):
                problems.append(
                    f"{rel}:{line_number}: {match.group(0)!r} cites a scratch document; "
                    "cite the ADR of the rule (docs/adr/README.md) or state the rule"
                )
    return problems


CHECKS: tuple[Callable[[Path], list[str]], ...] = (
    check_names,
    check_front_matter,
    check_titles_and_sections,
    check_supersession,
    check_readme,
    check_self_contained,
    check_adr_mentions,
    check_code_cites_adrs,
)


def report(problems: list[str], limit: int = 80) -> str:
    shown = problems[:limit]
    more = f"\n... and {len(problems) - limit} more" if len(problems) > limit else ""
    return f"{len(problems)} problem(s):\n" + "\n".join(shown) + more


# --- fixture trees ---------------------------------------------------------------------

FIXTURE_ADRS = {
    "0001-record-every-decision.md": """\
---
status: accepted
date: 2026-11-07
area: process
decided-by: owner
---

# ADR-0001: Every decision is recorded as an ADR

## Context

Decisions were scattered.

## Decision

Every decision gets a file in `docs/adr`.

## Consequences

The tests check the format.
""",
    "0002-import-whenever-the-table-is-empty.md": """\
---
status: superseded
date: 2026-11-06
area: shares
decided-by: agent
superseded-by: ADR-0003
---

# ADR-0002: Shares are imported whenever the table is empty

## Context

Superseded by ADR-0003: the import runs once per database.

The first design imported smb.conf whenever the shares table was empty.

## Decision

Import while `smb_shares` is empty.

## Consequences

Deleting the last share imported the old file again.
""",
    "0003-import-the-existing-smb-conf-once.md": """\
---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
supersedes: [ADR-0002]
---

# ADR-0003: The existing smb.conf is imported once

## Context

Deleting the last share imported the old file again (ADR-0002).

## Decision

A marker in `smb_state` makes the import run once per database.

```text
# not a heading: a line of a code block
```

## Consequences

A restored database brings its marker along.

## Considered options

- Import at every start: an edit outside UNOS would come back.
""",
}

FIXTURE_README = """\
# Architecture decision records

Every decision is recorded here ([ADR-0001](0001-record-every-decision.md)).

## Process

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0001](0001-record-every-decision.md) | Every decision is recorded as an ADR | accepted | 2026-11-07 |

## Shares

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0002](0002-import-whenever-the-table-is-empty.md) | Shares are imported whenever the table is empty | superseded by [0003](0003-import-the-existing-smb-conf-once.md) | 2026-11-06 |
| [0003](0003-import-the-existing-smb-conf-once.md) | The existing smb.conf is imported once | accepted | 2026-11-06 |
"""

FIXTURE_TEMPLATE = """\
---
status: proposed
date: YYYY-MM-DD
area: AREA
decided-by: OWNER-OR-AGENT
---

# ADR-NNNN: The decision in sentence case

## Context

## Decision

## Consequences
"""

# Files outside docs/adr that mention ADRs or could cite scratch documents.
FIXTURE_FILES = {
    "AGENTS.md": "Decisions: docs/adr (ADR-0001, [the import](docs/adr/0003-import-the-existing-smb-conf-once.md)).\n",
    "feature-list.json": json.dumps({"features": [{"id": "shares.import", "adr": ["ADR-0003"]}]}) + "\n",
    "supervisor/src/shares/service.py": '"""SMB shares."""\n\n# Imported once per database (ADR-0003).\nSPEC = None\n',
    "webui/tests/shares.spec.ts": "// The import runs once (ADR-0003).\n",
    ".agents/skills/adr/SKILL.md": "Record decisions (ADR-0001).\n",
    "docs/INSTALL.md": "See ADR-0002.\n",
}


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def good_tree(root: Path) -> Path:
    for name, text in FIXTURE_ADRS.items():
        write(root, f"{ADR_DIR}/{name}", text)
    write(root, f"{ADR_DIR}/README.md", FIXTURE_README)
    write(root, f"{ADR_DIR}/template.md", FIXTURE_TEMPLATE)
    for rel, text in FIXTURE_FILES.items():
        write(root, rel, text)
    return root


def edit(rel: str, old: str, new: str) -> Callable[[Path], None]:
    def change(root: Path) -> None:
        path = root / rel
        text = path.read_text(encoding="utf-8")
        assert text.count(old) == 1, f"{old!r} occurs {text.count(old)} times in {rel}"
        path.write_text(text.replace(old, new), encoding="utf-8")

    return change


def add(rel: str, text: str) -> Callable[[Path], None]:
    return lambda root: write(root, rel, text)


def rename(rel: str, new_rel: str) -> Callable[[Path], None]:
    return lambda root: (root / rel).rename(root / new_rel)


def remove(rel: str) -> Callable[[Path], None]:
    return lambda root: (root / rel).unlink()


A1 = f"{ADR_DIR}/0001-record-every-decision.md"
A2 = f"{ADR_DIR}/0002-import-whenever-the-table-is-empty.md"
A3 = f"{ADR_DIR}/0003-import-the-existing-smb-conf-once.md"
README = f"{ADR_DIR}/README.md"
ROW1 = "| [0001](0001-record-every-decision.md) | Every decision is recorded as an ADR | accepted | 2026-11-07 |\n"
TABLE_HEAD = "| ADR | Title | Status | Date |\n| --- | --- | --- | --- |\n"
_INTRO, _REST = FIXTURE_README.split("## Process\n")
SHARES_BEFORE_PROCESS = _INTRO + "## Shares" + _REST.split("## Shares")[1] + "\n## Process\n\n" + TABLE_HEAD + ROW1


def test_good_tree_has_no_problems(tmp_path):
    root = good_tree(tmp_path)
    for check in CHECKS:
        assert check(root) == [], check.__name__


def test_fixture_adrs_are_parsed(tmp_path):
    adrs = by_number(load_adrs(good_tree(tmp_path)))
    assert sorted(adrs) == [1, 2, 3]
    assert adrs[2].ids("superseded-by") == [3] and adrs[3].ids("supersedes") == [2]
    assert adrs[3].title() == "The existing smb.conf is imported once"
    assert [heading for heading, _, _ in adrs[3].sections()] == [*SECTIONS, OPTIONAL_LAST_SECTION]


BAD_CASES = [
    # names and numbering
    pytest.param(rename(A3, f"{ADR_DIR}/0004-import-once.md"), check_names, "missing: 0003", id="gap"),
    pytest.param(add(f"{ADR_DIR}/0003-another.md", FIXTURE_ADRS["0003-import-the-existing-smb-conf-once.md"]), check_names, "ADR-0003 is taken by 2 files", id="duplicate-number"),
    pytest.param(add(f"{ADR_DIR}/0004-Bad_Slug.md", "x\n"), check_names, "0004-Bad_Slug.md: not an ADR file name", id="bad-slug"),
    pytest.param(add(f"{ADR_DIR}/notes.md", "x\n"), check_names, "notes.md: not an ADR file name", id="stray-file"),
    pytest.param(add(f"{ADR_DIR}/0000-zero.md", "x\n"), check_names, "starts at 0001", id="number-zero"),
    pytest.param(remove(f"{ADR_DIR}/template.md"), check_names, "template.md is missing", id="no-template"),
    # front matter
    pytest.param(edit(A1, "decided-by: owner\n", ""), check_front_matter, "has no decided-by", id="missing-key"),
    pytest.param(edit(A1, "area: process\n", "area: process\ntags: [x]\n"), check_front_matter, "unknown key tags", id="unknown-key"),
    pytest.param(edit(A1, "status: accepted", "status: Accepted"), check_front_matter, "'Accepted' is not one of", id="bad-status"),
    pytest.param(edit(A1, "date: 2026-11-07", "date: 2026-13-01"), check_front_matter, "not a date", id="bad-date"),
    pytest.param(edit(A1, "date: 2026-11-07", "date: '2026-11-07'"), check_front_matter, "not a date", id="quoted-date"),
    pytest.param(edit(A1, "area: process", "area: smb"), check_front_matter, "'smb' is not one of", id="bad-area"),
    pytest.param(edit(A1, "decided-by: owner", "decided-by: lead"), check_front_matter, "'lead' is not one of", id="bad-decider"),
    pytest.param(edit(A2, "superseded-by: ADR-0003\n", ""), check_front_matter, "names its successor in superseded-by", id="superseded-without-successor"),
    pytest.param(edit(A1, "decided-by: owner\n", "decided-by: owner\nsuperseded-by: ADR-0003\n"), check_front_matter, "goes with status superseded", id="successor-without-status"),
    pytest.param(edit(A3, "supersedes: [ADR-0002]", "supersedes: ADR-0002"), check_front_matter, "list of ADR ids", id="supersedes-not-a-list"),
    pytest.param(edit(A3, "supersedes: [ADR-0002]", "supersedes: [ADR-2]"), check_front_matter, "list of ADR ids", id="supersedes-bad-id"),
    pytest.param(edit(A3, "supersedes: [ADR-0002]", "supersedes: [ADR-0002, ADR-0002]"), check_front_matter, "lists an ADR twice", id="supersedes-twice"),
    pytest.param(edit(A2, "superseded-by: ADR-0003", "superseded-by: [ADR-0003]"), check_front_matter, "one ADR id", id="successor-as-list"),
    pytest.param(edit(A1, "status: accepted\n", "status: accepted\nstatus: proposed\n"), check_front_matter, "status is given twice", id="duplicate-key"),
    pytest.param(edit(A1, "---\nstatus", "status"), check_front_matter, "starts with its front matter", id="no-front-matter"),
    pytest.param(edit(A1, "area: process\n", "area: process\n\n"), check_front_matter, "not 'key: value'", id="blank-line-in-front-matter"),
    # title and sections
    pytest.param(edit(A1, "# ADR-0001:", "# ADR-0004:"), check_titles_and_sections, "the title says ADR-0004", id="title-number"),
    pytest.param(edit(A1, "# ADR-0001: Every", "# Every"), check_titles_and_sections, "the first line after the front matter is the title", id="no-title"),
    pytest.param(edit(A1, "Every decision is recorded as an ADR", "Every Decision Is Recorded As An ADR"), check_titles_and_sections, "Title Case", id="title-case"),
    pytest.param(edit(A1, "recorded as an ADR\n", "recorded as an ADR.\n"), check_titles_and_sections, "ends with a period", id="title-period"),
    pytest.param(edit(A1, "## Consequences\n", "## Consequence\n"), check_titles_and_sections, "the sections are Context, Decision, Consequence", id="section-name"),
    pytest.param(edit(A1, "## Decision\n\nEvery decision gets a file in `docs/adr`.\n\n", ""), check_titles_and_sections, "the sections are Context, Consequences", id="missing-section"),
    pytest.param(edit(A3, "## Considered options", "## Notes"), check_titles_and_sections, "the sections are", id="extra-section"),
    pytest.param(edit(A1, "Decisions were scattered.\n", ""), check_titles_and_sections, "the section Context is empty", id="empty-section"),
    pytest.param(edit(A1, "The tests check the format.\n", "The tests check the format.\n\n# Notes\n"), check_titles_and_sections, "a second title", id="second-title"),
    # supersession
    pytest.param(edit(A3, "supersedes: [ADR-0002]\n", ""), check_supersession, "lists ADR-0002 in supersedes", id="one-sided-successor"),
    pytest.param(edit(A1, "decided-by: owner\n", "decided-by: owner\nsupersedes: [ADR-0002]\n"), check_supersession, "superseded-by: ADR-0001", id="one-sided-predecessor"),
    pytest.param(edit(A2, "superseded-by: ADR-0003", "superseded-by: ADR-0009"), check_supersession, "ADR-0009, which does not exist", id="unknown-successor"),
    pytest.param(edit(A3, "supersedes: [ADR-0002]", "supersedes: [ADR-0002, ADR-0007]"), check_supersession, "supersedes ADR-0007, which does not exist", id="unknown-predecessor"),
    pytest.param(edit(A1, "decided-by: owner\n", "decided-by: owner\nsupersedes: [ADR-0001]\n"), check_supersession, "does not supersede itself", id="supersedes-itself"),
    pytest.param(edit(A3, "date: 2026-11-06", "date: 2026-11-05"), check_supersession, "before ADR-0002", id="successor-older"),
    pytest.param(edit(A2, "Superseded by ADR-0003: the import runs once per database.\n\n", ""), check_supersession, "names ADR-0003 at the top", id="successor-not-named"),
    # README
    pytest.param(edit(README, ROW1, ""), check_readme, "no row for ADR-0001; add under '## Process': " + ROW1.strip(), id="missing-row"),
    pytest.param(edit(README, ROW1, ROW1 + ROW1), check_readme, "lists ADR-0001 again", id="duplicate-row"),
    pytest.param(edit(README, "| accepted | 2026-11-07 |", "| proposed | 2026-11-07 |"), check_readme, "the status of ADR-0001 is 'accepted'", id="wrong-status"),
    pytest.param(edit(README, "| superseded by [0003](0003-import-the-existing-smb-conf-once.md) |", "| superseded |"), check_readme, "the status of ADR-0002 is 'superseded by [0003]", id="status-without-successor"),
    pytest.param(edit(README, "| Every decision is recorded as an ADR |", "| Every decision is an ADR |"), check_readme, "the title of ADR-0001 is", id="wrong-title"),
    pytest.param(edit(README, "| accepted | 2026-11-07 |", "| accepted | 2026-11-08 |"), check_readme, "the date of ADR-0001 is 2026-11-07", id="wrong-date"),
    pytest.param(edit(README, "[0001](0001-record-every-decision.md) |", "[0001](0001-every-decision.md) |"), check_readme, "the file is 0001-record-every-decision.md", id="wrong-link"),
    pytest.param(edit(README, "## Process", "## Shares"), check_readme, "belongs under '## Process'", id="wrong-section"),
    pytest.param(edit(README, "## Process", "## Misc"), check_readme, "'Misc' is not an area heading", id="unknown-section"),
    pytest.param(edit(README, "| [0001](0001-record-every-decision.md) | Every", "| [0001](0001-record-every-decision.md) Every"), check_readme, "not a row", id="broken-row"),
    pytest.param(lambda root: write(root, README, SHARES_BEFORE_PROCESS), check_readme, "out of order", id="sections-out-of-order"),
    pytest.param(
        lambda root: write(root, README, FIXTURE_README.replace(
            "| [0002](0002-import-whenever-the-table-is-empty.md) | Shares are imported whenever the table is empty | superseded by [0003](0003-import-the-existing-smb-conf-once.md) | 2026-11-06 |\n"
            "| [0003](0003-import-the-existing-smb-conf-once.md) | The existing smb.conf is imported once | accepted | 2026-11-06 |\n",
            "| [0003](0003-import-the-existing-smb-conf-once.md) | The existing smb.conf is imported once | accepted | 2026-11-06 |\n"
            "| [0002](0002-import-whenever-the-table-is-empty.md) | Shares are imported whenever the table is empty | superseded by [0003](0003-import-the-existing-smb-conf-once.md) | 2026-11-06 |\n")),
        check_readme, "rows are in number order", id="rows-out-of-order",
    ),
    pytest.param(remove(README), check_readme, "README.md is missing", id="no-readme"),
    # self-contained
    pytest.param(edit(A1, "Decisions were scattered.", "Decisions were in /private/tmp/x/notes.md."), check_self_contained, "scratch folder", id="scratch-path"),
    pytest.param(edit(A1, "Decisions were scattered.", "Decisions were in the scratchpad."), check_self_contained, "scratch folder", id="scratchpad"),
    pytest.param(edit(A1, "Decisions were scattered.", "As spec 5.4 says."), check_self_contained, "'spec 5.4'", id="spec-section"),
    pytest.param(edit(A1, "Decisions were scattered.", "As D1 of decisions-v2 says."), check_self_contained, "'D1 of decisions'", id="decisions-v2"),
    pytest.param(edit(A1, "Decisions were scattered.", "As the decisions-v2 notes say."), check_self_contained, "'decisions-v2'", id="decisions-v2-notes"),
    pytest.param(edit(A1, "Decisions were scattered.", "As decision 11 says."), check_self_contained, "'decision 11'", id="numbered-decision"),
    pytest.param(edit(A1, "Decisions were scattered.", "Found in round 2."), check_self_contained, "'round 2'", id="review-round"),
    pytest.param(edit(A1, "Decisions were scattered.", "Row D7 says so."), check_self_contained, "'D7'", id="table-row"),
    pytest.param(edit(A1, "Decisions were scattered.", "C5 says so."), check_self_contained, "'C5'", id="brief-label"),
    pytest.param(edit(A1, "Decisions were scattered.", "Invariant I3 says so."), check_self_contained, "'I3'", id="invariant-label"),
    pytest.param(edit(A1, "Decisions were scattered.", "See BRIEF.md."), check_self_contained, "'BRIEF.md'", id="brief-file"),
    pytest.param(edit(A1, "Decisions were scattered.", "Found as S-F3."), check_self_contained, "'S-F3'", id="review-finding"),
    pytest.param(edit(A1, "Decisions were scattered.", "Решения были разбросаны."), check_self_contained, "Cyrillic", id="cyrillic"),
    pytest.param(add(f"{ADR_DIR}/template.md", FIXTURE_TEMPLATE + "Cite spec 2.4 here.\n"), check_self_contained, "template.md", id="template-too"),
    # ADR mentions
    pytest.param(edit("supervisor/src/shares/service.py", "(ADR-0003)", "(ADR-0004)"), check_adr_mentions, "service.py:3: ADR-0004 does not exist", id="unknown-id-in-code"),
    pytest.param(edit("feature-list.json", "ADR-0003", "ADR-0042"), check_adr_mentions, "feature-list.json:1: ADR-0042 does not exist", id="unknown-id-in-ledger"),
    pytest.param(edit("AGENTS.md", "(ADR-0001,", "(ADR-1,"), check_adr_mentions, "ADR-1 is not an ADR id", id="malformed-id"),
    pytest.param(edit("AGENTS.md", "0003-import-the-existing-smb-conf-once.md", "0003-import-once.md"), check_adr_mentions, "0003-import-once.md is not a file of docs/adr", id="stale-link"),
    pytest.param(edit(A3, "(ADR-0002)", "([the old rule](0002-import-when-empty.md))"), check_adr_mentions, "0002-import-when-empty.md is not a file of docs/adr", id="stale-relative-link"),
    pytest.param(edit("docs/INSTALL.md", "ADR-0002", "ADR-0005"), check_adr_mentions, "docs/INSTALL.md:1: ADR-0005", id="docs-too"),
    pytest.param(add("webui/AGENTS.md", "ADR-0005\n"), check_adr_mentions, "webui/AGENTS.md:1: ADR-0005", id="nested-agents-md"),
    pytest.param(add(".agents/skills/x/SKILL.md", "See docs/adr/0002-old-slug.md.\n"), check_adr_mentions, "0002-old-slug.md is not a file", id="stale-link-in-a-skill"),
    # the code cites ADRs
    pytest.param(edit("supervisor/src/shares/service.py", "(ADR-0003)", "(spec 1.4)"), check_code_cites_adrs, "service.py:3: 'spec 1.4'", id="spec-in-src"),
    pytest.param(add("supervisor/tests/test_x.py", '"""Spec 3.4: a foreign account."""\n'), check_code_cites_adrs, "test_x.py:1: 'Spec 3.4'", id="capitalized-spec"),
    pytest.param(add("supervisor/tests/test_y.py", "# D1 of decisions-v2\n"), check_code_cites_adrs, "'D1 of decisions'", id="decisions-v2-in-tests"),
    pytest.param(add("webui/src/lib/x.ts", "// spec-v2 says\n"), check_code_cites_adrs, "'spec-v2'", id="webui-src"),
    pytest.param(edit("webui/tests/shares.spec.ts", "(ADR-0003)", "(decision 3 of the brief)"), check_code_cites_adrs, "'decision 3 of'", id="webui-tests"),
    pytest.param(add("supervisor/tests/test_z.py", '"""R-F17: a lost commit."""\n'), check_code_cites_adrs, "test_z.py:1: 'R-F17'", id="review-finding-in-tests"),
]


@pytest.mark.parametrize(("mutate", "check", "expected"), BAD_CASES)
def test_bad_case_is_reported(tmp_path, mutate, check, expected):
    root = good_tree(tmp_path)
    mutate(root)
    problems = check(root)
    assert any(expected in problem for problem in problems), report(problems)


def test_missing_adr_dir_is_one_problem_per_check(tmp_path):
    assert check_names(tmp_path) == ["docs/adr is missing"]
    assert check_readme(tmp_path) == ["docs/adr/README.md is missing"]
    for check in (check_front_matter, check_titles_and_sections, check_supersession, check_self_contained):
        assert check(tmp_path) == []


def test_scans_skip_what_they_must_not_read(tmp_path):
    """Secrets files, dependencies, caches and this file itself are not scanned."""
    root = good_tree(tmp_path)
    for rel in ("scripts/dev/dev.env", "webui/tests/node_modules/x/index.js", "supervisor/.venv/lib/x.py",
                "supervisor/tests/__pycache__/x.py", SELF):
        write(root, rel, "ADR-0999 and spec 5.4\n")
    write(root, "supervisor/tests/data.bin", "ADR-0999\0spec 5.4\n")
    assert check_adr_mentions(root) == []
    assert check_code_cites_adrs(root) == []


def test_code_may_use_an_adr_file_path_as_data(tmp_path):
    """Only Markdown links to ADR files are checked: code cites ids, and tests use paths as data."""
    root = good_tree(tmp_path)
    write(root, "supervisor/tests/test_paths.py", 'PATHS = ["docs/adr/0001-x.md"]\n')
    assert check_adr_mentions(root) == []


def test_line_numbers_are_those_of_grep(tmp_path):
    """A line separator inside a string (U+2028) does not shift the reported line."""
    root = good_tree(tmp_path)
    write(root, "supervisor/src/names.py", 'SEPARATORS = "  "\n# As spec 5.4 says (ADR-0009).\n')
    assert check_code_cites_adrs(root) == [
        "supervisor/src/names.py:2: 'spec 5.4' cites a scratch document; "
        "cite the ADR of the rule (docs/adr/README.md) or state the rule"
    ]
    assert check_adr_mentions(root) == ["supervisor/src/names.py:2: ADR-0009 does not exist in docs/adr"]


def test_code_scan_is_limited_to_code(tmp_path):
    """docs/ may name a scratch label in prose (it is not code); ADRs are checked separately."""
    root = good_tree(tmp_path)
    write(root, "docs/TESTING.md", "Once known as spec 9.4.\n")
    assert check_code_cites_adrs(root) == []


@pytest.mark.parametrize(
    ("title", "problem"),
    [
        ("Every decision is recorded as an ADR", None),
        ("The API takes Bearer tokens, not cookies, and refuses cross-origin requests by default", None),
        ("PyJWT and bcrypt replace python-jose and passlib, and dependencies are pinned", None),
        ("NetBIOS stays until a discovery slice adds WS-Discovery and Bonjour", None),
        ("`make check` is the single quality gate", None),
        ("UNOS Runs On An Existing Linux Host", "Title Case"),
        ("pools use btrfs", "lowercase"),
        ("Pools use btrfs.", "period"),
        ("Pools use btrfs | ext4", "'|'"),
    ],
)
def test_sentence_case(title, problem):
    found = sentence_case_problem(title)
    assert (found is None) if problem is None else (problem in (found or ""))


# --- this repository ---------------------------------------------------------------------


@pytest.mark.parametrize("check", CHECKS, ids=lambda check: check.__name__)
def test_repository(check):
    problems = check(REPO)
    assert not problems, report(problems)
