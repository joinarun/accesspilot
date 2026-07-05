import difflib
import shutil
from pathlib import Path

from bs4 import BeautifulSoup

from app.schemas import (
    AccessibilityIssue,
    PatchProposal,
    RepairAction,
    RiskLevel,
    Severity,
    SourceLocation,
    SourceMapping,
)
from app.services.repair import repair_engine


def _issue(rule: str, issue_id: str, selector: str) -> AccessibilityIssue:
    return AccessibilityIssue(
        id=issue_id,
        rule_id=rule,
        title=rule,
        severity=Severity.SERIOUS,
        page_url="http://example.test/",
        route="/",
        selector=selector,
        explanation="Fixture failure",
        suggested_fix="Apply a semantic repair.",
        confidence=0.98,
    )


def test_repair_engine_applies_semantic_fixture_fixes(tmp_path: Path) -> None:
    fixture = Path("examples/vulnerable-app")
    workspace = tmp_path / "workspace"
    shutil.copytree(fixture, workspace)
    issues = [
        _issue("html-has-lang", "lang", "html"),
        _issue("image-alt", "image", "img.hero-image"),
        _issue("label", "project-label", "#project-name"),
        _issue("label", "email-label", "#email"),
        _issue("heading-order", "heading", ".features h3"),
        _issue("non-semantic-interactive", "control", "#start-action"),
        _issue("accessible-error-message", "error", "#form-error"),
        _issue("landmark-one-main", "main", "html"),
        _issue("focus-visible", "focus", "*:focus"),
        _issue("color-contrast", "contrast", ".muted-copy"),
    ]
    mappings = [
        SourceMapping(
            issue_id=issue.id,
            selected_path="styles.css"
            if issue.rule_id in {"focus-visible", "color-contrast"}
            else "index.html",
            candidates=[
                SourceLocation(
                    path="styles.css"
                    if issue.rule_id in {"focus-visible", "color-contrast"}
                    else "index.html",
                    confidence=0.95,
                )
            ],
            confidence=0.95,
        )
        for issue in issues
    ]
    plan = repair_engine.plan(workspace, issues, mappings)
    changes = repair_engine.apply(workspace, plan, issues)

    assert {change.path for change in changes} == {"index.html", "styles.css"}
    soup = BeautifulSoup((workspace / "index.html").read_text(), "html.parser")
    assert soup.html["lang"] == "en"
    assert soup.select_one("img.hero-image")["alt"] == "Workspace"
    labels = {
        label["for"]: label.get_text(strip=True)
        for label in soup.select("label.accesspilot-label")
    }
    assert labels == {"project-name": "Project name", "email": "Email"}
    assert soup.select_one("#start-action").name == "button"
    assert soup.main is not None
    assert soup.select_one("#form-error")["role"] == "alert"
    css = (workspace / "styles.css").read_text()
    assert "outline: none" not in css
    assert "#475467" in css
    assert "#6941c6" in css
    assert "#c7cbd4" not in css
    assert "#c4b5fd" not in css


def test_repair_engine_handles_tsx_and_plan_bound_agent_patch(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    component = workspace / "Signup.tsx"
    before = (
        "export function Signup() {\n"
        '  return <><img src="/team-photo.png" /><input id="email" '
        'placeholder="Work email" /></>;\n'
        "}\n"
    )
    component.write_text(before, encoding="utf-8")
    test_file = workspace / "Signup.test.tsx"
    test_before = 'it("renders signup", () => expect(true).toBe(true));\n'
    test_after = 'it("labels signup email", () => expect(true).toBe(true));\n'
    test_file.write_text(test_before, encoding="utf-8")
    issues = [
        _issue("image-alt", "image", "img"),
        _issue("label", "label", "#email"),
    ]
    mappings = [
        SourceMapping(
            issue_id=issue.id,
            selected_path="Signup.tsx",
            candidates=[SourceLocation(path="Signup.tsx", confidence=0.95)],
            confidence=0.95,
        )
        for issue in issues
    ]
    plan = repair_engine.plan(workspace, issues, mappings)
    plan.actions.append(
        RepairAction(
            id="test-update",
            issue_ids=["label"],
            file_path="Signup.test.tsx",
            strategy="Add a focused accessible-name regression test.",
            rationale="Planner-authorized test update.",
            change_kind="test",
            risk=RiskLevel.LOW,
            confidence=0.8,
            expected_result="The label remains covered by a focused test.",
            automatic=False,
        )
    )
    proposed = before.replace("export function Signup", "export function SignupForm")
    proposal = PatchProposal(
        issue_ids=["image"],
        path="Signup.tsx",
        change_summary="Use the project naming convention",
        unified_diff="".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                proposed.splitlines(keepends=True),
                fromfile="a/Signup.tsx",
                tofile="b/Signup.tsx",
            )
        ),
    )
    test_proposal = PatchProposal(
        issue_ids=["label"],
        path="Signup.test.tsx",
        change_summary="Add focused label coverage",
        unified_diff="".join(
            difflib.unified_diff(
                test_before.splitlines(keepends=True),
                test_after.splitlines(keepends=True),
                fromfile="a/Signup.test.tsx",
                tofile="b/Signup.test.tsx",
            )
        ),
    )

    changes = repair_engine.apply(workspace, plan, issues, [proposal, test_proposal])

    assert {change.path for change in changes} == {
        "Signup.tsx",
        "Signup.test.tsx",
    }
    result = component.read_text(encoding="utf-8")
    assert "SignupForm" in result
    assert 'alt="Team photo"' in result
    assert 'htmlFor="email"' in result
    assert 'className="accesspilot-label"' in result
    assert "labels signup email" in test_file.read_text(encoding="utf-8")
