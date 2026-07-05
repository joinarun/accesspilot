from app.schemas import (
    AccessibilityIssue,
    AxeNode,
    AxeViolation,
    PageScanResult,
    Severity,
)
from app.services.normalize import issue_fingerprint, normalize_scan_results
from app.services.verification import verification_service


def test_fingerprint_is_stable_when_dom_markup_changes() -> None:
    before = issue_fingerprint("label", "/signup", "#email", '<input id="email">')
    after = issue_fingerprint(
        "label", "/signup", "#email", '<input id="email" aria-describedby="error">'
    )
    assert before == after


def test_normalizer_preserves_evidence_and_wcag_reference() -> None:
    scan = PageScanResult(
        url="https://example.test/",
        route="/",
        violations=[
            AxeViolation(
                id="image-alt",
                impact="critical",
                tags=["wcag2a", "wcag111"],
                help="Images must have alternate text",
                nodes=[
                    AxeNode(
                        target=["img.hero"],
                        html='<img class="hero">',
                        failure_summary="Fix the missing alt attribute.",
                    )
                ],
            )
        ],
    )
    issue = normalize_scan_results([scan])[0]
    assert issue.severity.value == "critical"
    assert issue.wcag_references == ["1.1.1"]
    assert issue.selector == "img.hero"
    assert issue.dom_snippet == '<img class="hero">'


def test_verification_cannot_pass_with_invalid_scan_evidence() -> None:
    baseline = [
        AccessibilityIssue(
            id="issue-1",
            rule_id="image-alt",
            title="Missing image text",
            severity=Severity.CRITICAL,
            page_url="https://example.test/",
            route="/",
            selector="img",
            explanation="Missing alt text.",
            suggested_fix="Add appropriate alt text.",
        )
    ]

    report = verification_service.compare(
        baseline,
        [],
        [],
        [],
        scan_evidence_valid=False,
        scan_errors=["navigation failed"],
    )

    assert report.fixed_issue_count == 1
    assert report.passed is False
    assert report.scan_evidence_valid is False
