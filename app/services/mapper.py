"""Map rendered DOM evidence back to likely source files."""

from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup

from app.schemas import AccessibilityIssue, SourceLocation, SourceMapping
from app.settings import AccessPilotSettings, get_settings

from .safety import redact_text

_SOURCE_SUFFIXES = {".html", ".htm", ".jsx", ".tsx", ".vue", ".svelte", ".css", ".scss"}
_SKIP_PARTS = {"node_modules", ".git", ".venv", "dist", "build", ".next", "coverage"}
_MIN_SELECTED_CONFIDENCE = 0.6


class SourceMapper:
    def __init__(self, settings: AccessPilotSettings | None = None) -> None:
        self.settings = settings or get_settings()

    def map_issues(
        self, root: Path, issues: list[AccessibilityIssue]
    ) -> list[SourceMapping]:
        files = self._source_files(root)
        cache: dict[Path, str] = {}
        mappings: list[SourceMapping] = []
        for issue in issues:
            candidates = self._map_issue(root, files, cache, issue)
            candidates = sorted(
                candidates, key=lambda value: value.confidence, reverse=True
            )[:5]
            issue.source_locations = candidates
            mappings.append(
                SourceMapping(
                    issue_id=issue.id,
                    candidates=candidates,
                    selected_path=(
                        candidates[0].path
                        if candidates
                        and candidates[0].confidence >= _MIN_SELECTED_CONFIDENCE
                        else None
                    ),
                    rationale=(
                        "Matched stable DOM attributes or text in the isolated source copy."
                        if candidates
                        and candidates[0].confidence >= _MIN_SELECTED_CONFIDENCE
                        else "No source match met the automatic confidence threshold."
                    ),
                    confidence=(
                        candidates[0].confidence
                        if candidates
                        and candidates[0].confidence >= _MIN_SELECTED_CONFIDENCE
                        else 0.0
                    ),
                )
            )
        return mappings

    def _source_files(self, root: Path) -> list[Path]:
        files: list[Path] = []
        for path in root.rglob("*"):
            if len(files) >= 1000:
                break
            if not path.is_file() or path.suffix.lower() not in _SOURCE_SUFFIXES:
                continue
            if any(part in _SKIP_PARTS for part in path.relative_to(root).parts):
                continue
            try:
                if path.stat().st_size > self.settings.max_source_file_bytes:
                    continue
            except OSError:
                continue
            files.append(path)
        return files

    def _map_issue(
        self,
        root: Path,
        files: list[Path],
        cache: dict[Path, str],
        issue: AccessibilityIssue,
    ) -> list[SourceLocation]:
        clues = _dom_clues(issue.dom_snippet or "")
        if issue.selector and issue.selector != "document":
            clues.extend(_selector_clues(issue.selector))
        candidates: dict[Path, SourceLocation] = {}
        for path in files:
            try:
                text = cache.setdefault(path, path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
            score, position = _score_text(text, clues)
            if issue.selector == "document" and path.name in {
                "index.html",
                "layout.tsx",
                "_document.tsx",
            }:
                score = max(score, 0.82)
                position = max(position, 0)
            if score < 0.45:
                continue
            line = text.count("\n", 0, max(position, 0)) + 1
            lines = text.splitlines()
            snippet = (
                lines[line - 1].strip()[:500] if lines and line <= len(lines) else None
            )
            relative = path.relative_to(root).as_posix()
            location = SourceLocation(
                path=relative,
                line=line,
                snippet=redact_text(snippet, limit=500) if snippet else None,
                confidence=min(score, 0.99),
            )
            previous = candidates.get(path)
            if previous is None or location.confidence > previous.confidence:
                candidates[path] = location
        return list(candidates.values())


def _dom_clues(snippet: str) -> list[tuple[str, float]]:
    if not snippet:
        return []
    soup = BeautifulSoup(snippet, "html.parser")
    element = soup.find()
    if not element:
        return []
    clues: list[tuple[str, float]] = []
    for key in (
        "id",
        "name",
        "data-testid",
        "aria-label",
        "placeholder",
        "src",
        "href",
    ):
        value = element.attrs.get(key)
        if isinstance(value, str) and len(value) >= 2:
            clues.append((value, 0.78 if key in {"id", "data-testid"} else 0.64))
    classes = element.attrs.get("class", [])
    if isinstance(classes, list):
        clues.extend((value, 0.55) for value in classes if len(value) >= 3)
    text = element.get_text(" ", strip=True)
    if 2 <= len(text) <= 100:
        clues.append((text, 0.66))
    clues.append((f"<{element.name}", 0.2))
    return clues


def _selector_clues(selector: str) -> list[tuple[str, float]]:
    clues: list[tuple[str, float]] = []
    for match in re.finditer(r"#([A-Za-z][\w-]*)", selector):
        clues.append((match.group(1), 0.75))
    for match in re.finditer(r"\.([A-Za-z][\w-]*)", selector):
        clues.append((match.group(1), 0.5))
    return clues


def _score_text(text: str, clues: list[tuple[str, float]]) -> tuple[float, int]:
    score = 0.0
    first_position = -1
    for clue, weight in clues:
        position = text.find(clue)
        if position < 0:
            continue
        if first_position < 0:
            first_position = position
        score += weight
    return min(score, 0.99), first_position


source_mapper = SourceMapper()
