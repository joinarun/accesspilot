"""Normalize deterministic scanner output into stable accessibility issues."""

from __future__ import annotations

import hashlib
import re
from html import unescape

from app.schemas import (
    AccessibilityIssue,
    AxeNode,
    PageScanResult,
    Severity,
)

from .safety import redact_text

_STRATEGIES = {
    "html-has-lang": "Add the document's primary language to the html element.",
    "document-title": "Add a concise, unique title element to the document head.",
    "image-alt": "Give informative images meaningful alt text and decorative images empty alt text.",
    "label": "Associate a visible label with each form control using for and id.",
    "label-title-only": "Associate a visible label with the form control instead of relying on placeholder or title text.",
    "heading-order": "Use heading levels that reflect the page hierarchy without skipped levels.",
    "button-name": "Use a native button with an accessible text name.",
    "link-name": "Give the link a descriptive accessible name.",
    "color-contrast": "Adjust foreground or background colors to meet the requested WCAG contrast ratio.",
    "landmark-one-main": "Wrap the page's primary content in one main landmark.",
    "region": "Place meaningful page content inside an appropriate landmark.",
    "non-semantic-interactive": "Replace the clickable generic element with a native button or link and retain keyboard behavior.",
    "focus-visible": "Restore a clearly visible :focus-visible indicator with sufficient contrast.",
    "accessible-error-message": "Associate validation feedback with its control and announce updates through a polite live region.",
}


def normalize_scan_results(scans: list[PageScanResult]) -> list[AccessibilityIssue]:
    issues: list[AccessibilityIssue] = []
    seen: set[str] = set()
    for scan in scans:
        for violation in scan.violations:
            nodes = violation.nodes or [AxeNode(target=["document"])]
            for node in nodes:
                selector = ", ".join(node.target) or "document"
                issue_id = issue_fingerprint(
                    violation.id, scan.route, selector, node.html
                )
                if issue_id in seen:
                    continue
                seen.add(issue_id)
                summary = _plain_text(node.failure_summary or violation.description)
                issues.append(
                    AccessibilityIssue(
                        id=issue_id,
                        rule_id=violation.id,
                        title=violation.help or _title_from_rule(violation.id),
                        severity=_severity(node.impact or violation.impact),
                        wcag_references=_wcag_references(violation.tags),
                        page_url=scan.url,
                        route=scan.route,
                        selector=selector,
                        screenshot_path=scan.screenshot_path,
                        dom_snippet=(
                            redact_text(node.html[:2000]) if node.html else None
                        ),
                        explanation=summary
                        or "The deterministic scanner found an accessibility barrier.",
                        suggested_fix=_STRATEGIES.get(
                            violation.id,
                            "Review the failing element and correct the underlying semantic or interaction behavior.",
                        ),
                        confidence=0.98 if violation.id in _STRATEGIES else 0.9,
                        help_url=violation.help_url,
                        tags=violation.tags,
                    )
                )
    return sorted(
        issues,
        key=lambda issue: (
            _severity_rank(issue.severity),
            issue.route,
            issue.rule_id,
            issue.selector,
        ),
    )


def issue_fingerprint(rule_id: str, route: str, selector: str, html: str) -> str:
    # DOM snippets are deliberately excluded: source repairs often change the
    # snippet while the same unresolved rule/selector remains. Stable identity
    # is required for before/after comparison and loop termination.
    raw = "\x1f".join((rule_id, route.rstrip("/") or "/", selector.strip()))
    return f"a11y-{hashlib.sha256(raw.encode()).hexdigest()[:16]}"


def issue_set_fingerprint(issues: list[AccessibilityIssue]) -> str:
    value = "\n".join(sorted(issue.id for issue in issues))
    return hashlib.sha256(value.encode()).hexdigest()


def _severity(value: str | None) -> Severity:
    try:
        return Severity(value or "unknown")
    except ValueError:
        return Severity.UNKNOWN


def _severity_rank(value: Severity) -> int:
    return {
        Severity.CRITICAL: 0,
        Severity.SERIOUS: 1,
        Severity.MODERATE: 2,
        Severity.MINOR: 3,
        Severity.UNKNOWN: 4,
    }[value]


def _title_from_rule(rule_id: str) -> str:
    return rule_id.replace("-", " ").strip().capitalize()


def _plain_text(value: str) -> str:
    value = unescape(re.sub(r"<[^>]+>", " ", value or ""))
    return re.sub(r"\s+", " ", value).strip()


def _wcag_references(tags: list[str]) -> list[str]:
    references: list[str] = []
    for tag in tags:
        match = re.fullmatch(r"wcag(\d)(\d)(\d)", tag.lower())
        if match:
            reference = ".".join(match.groups())
            if reference not in references:
                references.append(reference)
    return references
