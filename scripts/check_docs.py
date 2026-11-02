"""Offline documentation checks for this repository.

Checks that relative links and anchors resolve, that every English page has a Simplified
Chinese page with the same heading structure, that both READMEs carry the four-name index
and the install command on their first screen, that no stale repository name, retired host
or moved path remains, that version pins match engine-baseline.json, and that
engine-baseline.json is well formed. Prints one compact JSON line; exit 0 = clean, 1 = problems.

    python scripts/check_docs.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = "engine-baseline.json"
SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules"}
TEXT_SUFFIXES = {".md", ".yml", ".yaml", ".json"}

INDEX_LINKS = (
    r"https://pypi\.org/project/os-optics/",
    r"https://pypi\.org/project/os-optics-kit/",
    r"https://github\.com/JunLin615/os-optics(?![\w-])",
    r"https://github\.com/JunLin615/os-optics-kit(?![\w-])",
)
INSTALL_COMMAND = 'python -m pip install "os-optics[protected]"'
STALE = (
    (r"JunLin615/opensci-" + "engine", "earlier repository name"),
    (r"(?<![\w./-])examples/", "path moved to os-optics-kit"),
    (r"requirements-" + r"examples\.txt", "file moved to os-optics-kit"),
)
PAIRS = (("README.md", "README.zh-CN.md"),)

FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
LINK = re.compile(r"!?\[(?:[^\[\]]|\[[^\]]*\])*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
HTML_REF = re.compile(r"""(?:href|src)=["']([^"']+)["']""")
PIN = re.compile(r"os-optics(?:\[[a-z,]+\])?==([0-9][0-9A-Za-z.+-]*)")
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def text_files() -> list[Path]:
    files = []
    for path in sorted(ROOT.rglob("*")):
        rel = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts[:-1]) or not path.is_file():
            continue
        if rel.parts[0].startswith(".") and rel.parts[0] != ".github":
            continue
        if path.suffix in TEXT_SUFFIXES:
            files.append(path)
    return files


def prose_lines(text: str) -> list[tuple[int, str]]:
    """Lines outside fenced code blocks, with 1-based numbers."""
    lines, fenced = [], False
    for number, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            lines.append((number, line))
    return lines


def headings(text: str) -> list[tuple[int, str]]:
    return [(len(m.group(1)), m.group(2)) for _, line in prose_lines(text) if (m := HEADING.match(line))]


def slug(title: str) -> str:
    title = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", title)
    return re.sub(r"[^\w\- ]", "", title.strip().lower()).replace(" ", "-")


def anchors(path: Path) -> set[str]:
    seen: dict[str, int] = {}
    result = set()
    for _, title in headings(path.read_text(encoding="utf-8")):
        base = slug(title)
        count = seen.get(base, 0)
        seen[base] = count + 1
        result.add(base if count == 0 else f"{base}-{count}")
    return result


def check_links(path: Path, problems: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    rel = path.relative_to(ROOT).as_posix()
    for number, line in prose_lines(text):
        for target in LINK.findall(line) + HTML_REF.findall(line):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
                continue
            file_part, _, anchor = target.partition("#")
            destination = (path.parent / file_part).resolve() if file_part else path
            if not destination.exists():
                problems.append(f"{rel}:{number}: link target not found: {target}")
                continue
            try:
                destination.relative_to(ROOT)
            except ValueError:
                problems.append(f"{rel}:{number}: link leaves the repository: {target}")
                continue
            if anchor and destination.suffix == ".md" and anchor not in anchors(destination):
                problems.append(f"{rel}:{number}: anchor not found: {target}")


def check_pairs(problems: list[str]) -> None:
    pairs = list(PAIRS)
    english, chinese = ROOT / "docs" / "en", ROOT / "docs" / "zh-CN"
    names = {p.name for p in english.glob("*.md")} | {p.name for p in chinese.glob("*.md")}
    pairs += [(f"docs/en/{name}", f"docs/zh-CN/{name}") for name in sorted(names)]
    for first, second in pairs:
        a, b = ROOT / first, ROOT / second
        if not (a.exists() and b.exists()):
            problems.append(f"missing language pair: {first} / {second}")
            continue
        levels_a = [level for level, _ in headings(a.read_text(encoding="utf-8"))]
        levels_b = [level for level, _ in headings(b.read_text(encoding="utf-8"))]
        if levels_a != levels_b:
            problems.append(f"heading structure differs: {first} {levels_a} / {second} {levels_b}")


def check_index(problems: list[str]) -> None:
    for name in ("README.md", "README.zh-CN.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        first_screen = text.split("\n## ", 1)[0]
        for pattern in INDEX_LINKS:
            if not re.search(pattern, first_screen):
                problems.append(f"{name}: four-name index lacks {pattern.replace(chr(92), '')} before the first section")
        if INSTALL_COMMAND not in first_screen:
            problems.append(f"{name}: install command missing before the first section")


def check_baseline(problems: list[str]) -> dict:
    try:
        baseline = json.loads((ROOT / BASELINE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        problems.append(f"{BASELINE}: unreadable: {error}")
        return {}
    expected = {"schema_version": int, "distribution": str, "version": str, "extra": str,
                "website": str, "retired_hosts": list}
    for key, kind in expected.items():
        if not isinstance(baseline.get(key), kind):
            problems.append(f"{BASELINE}: '{key}' missing or not {kind.__name__}")
    if baseline.get("schema_version") != 2:
        problems.append(f"{BASELINE}: schema_version must be 2")
    if not SEMVER.match(str(baseline.get("version", ""))):
        problems.append(f"{BASELINE}: version is not MAJOR.MINOR.PATCH")
    if not str(baseline.get("website", "")).startswith("https://"):
        problems.append(f"{BASELINE}: website must be an https URL")
    return baseline


def check_content(files: list[Path], baseline: dict, problems: list[str]) -> None:
    stale = list(STALE)
    for host in baseline.get("retired_hosts") or []:
        stale.append((r"(?<![\w.-])" + re.escape(host) + r"(?![\w-])", "retired host"))
    version = baseline.get("version")
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        if rel == BASELINE:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for pattern, reason in stale:
                if re.search(pattern, line):
                    problems.append(f"{rel}:{number}: {reason}: {line.strip()[:120]}")
            for pinned in PIN.findall(line):
                if pinned != version:
                    problems.append(f"{rel}:{number}: pins os-optics {pinned}, baseline is {version}")


def main() -> int:
    problems: list[str] = []
    files = text_files()
    baseline = check_baseline(problems)
    for path in files:
        if path.suffix == ".md":
            check_links(path, problems)
    check_pairs(problems)
    check_index(problems)
    check_content(files, baseline, problems)
    print(json.dumps({"status": "FAIL" if problems else "OK", "files": len(files),
                      "baseline": baseline.get("version"), "problems": problems},
                     ensure_ascii=False, separators=(",", ":")))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
