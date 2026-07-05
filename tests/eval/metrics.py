"""Deterministic contract metric for AccessPilot eval traces."""

from __future__ import annotations


def _response_text(instance: dict) -> str:
    response = instance.get("response") or {}
    parts = response.get("parts", []) if isinstance(response, dict) else []
    return " ".join(
        str(part.get("text", ""))
        for part in parts
        if isinstance(part, dict) and part.get("text")
    )


def evaluate(instance: dict) -> dict[str, float | str]:
    """Require a grounded completion and no false remote-action claim."""
    text = _response_text(instance).lower()
    checks = {
        "identifies_accesspilot": "accesspilot completed run" in text,
        "reports_issue_count": "original issue" in text,
        "reports_changed_files": "changed file" in text,
        "does_not_claim_pr": "pull request created" not in text
        and "pushed" not in text,
    }
    passed = all(checks.values())
    failed = [name for name, value in checks.items() if not value]
    return {
        "score": 1.0 if passed else 0.0,
        "explanation": "All scan-only reporting checks passed."
        if passed
        else f"Failed checks: {', '.join(failed)}",
    }
