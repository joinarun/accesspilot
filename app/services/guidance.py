"""Bounded repository-convention discovery for planning and repair agents."""

from __future__ import annotations

from pathlib import Path

from .safety import redact_text

_GUIDANCE_PATHS = (
    "AGENTS.md",
    "CONTRIBUTING.md",
    ".github/CONTRIBUTING.md",
    "docs/CONTRIBUTING.md",
)
_PER_FILE_LIMIT = 8_000
_TOTAL_LIMIT = 20_000


def load_repository_guidance(root: Path | None) -> list[str]:
    if root is None:
        return []
    guidance: list[str] = []
    remaining = _TOTAL_LIMIT
    for relative in _GUIDANCE_PATHS:
        path = root / relative
        if not path.is_file() or path.is_symlink() or remaining <= 0:
            continue
        try:
            text = path.read_text(encoding="utf-8")[: min(_PER_FILE_LIMIT, remaining)]
        except (OSError, UnicodeDecodeError):
            continue
        guidance.append(f"{relative}:\n{redact_text(text)}")
        remaining -= len(text)
    return guidance


__all__ = ["load_repository_guidance"]
