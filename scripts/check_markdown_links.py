#!/usr/bin/env python
"""Fail when a repository Markdown document points at a missing local path."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlsplit
import re


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "novels",
    "70_runtime",
    "site-packages",
}
INLINE_LINK = re.compile(
    r"!?\[[^\]\n]*\]\(\s*(<[^>\n]+>|[^)\s]+)"
    r"(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*\)"
)
REFERENCE_LINK = re.compile(r"^\s*\[[^\]\n]+\]:\s*(<[^>\n]+>|\S+)")


def markdown_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.md")
        if not EXCLUDED_PARTS.intersection(path.relative_to(root).parts)
    )


def local_targets(text: str) -> list[tuple[int, str]]:
    targets: list[tuple[int, str]] = []
    in_fence = False
    fence_marker = ""
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            continue
        if in_fence:
            continue
        matches = [match.group(1) for match in INLINE_LINK.finditer(line)]
        reference = REFERENCE_LINK.match(line)
        if reference:
            matches.append(reference.group(1))
        for raw_target in matches:
            target = raw_target.strip().removeprefix("<").removesuffix(">")
            if is_local_target(target):
                targets.append((line_number, target))
    return targets


def is_local_target(target: str) -> bool:
    if not target or target.startswith(("#", "//")):
        return False
    parsed = urlsplit(target)
    return not parsed.scheme and not parsed.netloc


def resolve_local_target(root: Path, document: Path, target: str) -> Path | None:
    path_text = unquote(target.split("#", 1)[0].split("?", 1)[0]).replace("\\", "/")
    if not path_text:
        return document
    candidate = root / path_text.lstrip("/") if path_text.startswith("/") else document.parent / path_text
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


def has_exact_case(root: Path, target: Path) -> bool:
    if not target.exists():
        return False
    current = root
    for part in target.relative_to(root).parts:
        try:
            names = {child.name for child in current.iterdir()}
        except OSError:
            return False
        if part not in names:
            return False
        current /= part
    return True


def main() -> int:
    errors: list[str] = []
    documents = markdown_files(ROOT)
    checked = 0
    for document in documents:
        try:
            text = document.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append(f"{document.relative_to(ROOT).as_posix()}: unreadable Markdown: {exc}")
            continue
        for line_number, raw_target in local_targets(text):
            checked += 1
            target = resolve_local_target(ROOT, document, raw_target)
            if target is None:
                errors.append(
                    f"{document.relative_to(ROOT).as_posix()}:{line_number}: "
                    f"local link escapes repository: {raw_target}"
                )
            elif not has_exact_case(ROOT, target):
                errors.append(
                    f"{document.relative_to(ROOT).as_posix()}:{line_number}: "
                    f"missing or case-mismatched local link: {raw_target}"
                )
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        print(f"Markdown local links failed: {len(errors)} error(s), {checked} link(s) checked.")
        return 1
    print(f"Markdown local links passed: {len(documents)} document(s), {checked} link(s) checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
