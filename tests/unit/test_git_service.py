from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
from pathlib import Path

import pytest

from app.schemas import (
    AccessibilityIssue,
    AccessPilotRequest,
    ApprovalDecision,
    ApprovalRecord,
    FileChange,
    PullRequestDraft,
    ResolvedTarget,
    Severity,
    TargetKind,
    VerificationReport,
    WorkflowState,
)
from app.services.errors import ApprovalRequired
from app.services.git_service import GitService
from app.services.verification import verification_fingerprint

_BEFORE = "<html><body>before</body></html>\n"
_AFTER = '<html lang="en"><body>after</body></html>\n'


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _git(root: Path, *argv: str) -> str:
    return subprocess.run(
        ["git", *argv],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _prepare_repository(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repository"
    remote = tmp_path / "remote.git"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "AccessPilot Test")
    _git(root, "config", "user.email", "accesspilot@example.test")
    (root / "index.html").write_text(_BEFORE, encoding="utf-8")
    (root / "notes.txt").write_text("reviewed baseline\n", encoding="utf-8")
    _git(root, "add", "index.html", "notes.txt")
    _git(root, "commit", "-m", "baseline")
    _git(tmp_path, "init", "--bare", str(remote))
    _git(root, "remote", "add", "origin", str(remote))
    (root / "index.html").write_text(_AFTER, encoding="utf-8")
    return root, remote


def _reviewed_run(
    root: Path,
    *,
    verification_passed: bool = True,
) -> tuple[WorkflowState, ApprovalRecord]:
    change = FileChange(
        path="index.html",
        issue_ids=["html-has-lang"],
        summary="add document language",
        before_sha256=_sha256(_BEFORE),
        after_sha256=_sha256(_AFTER),
        additions=1,
        deletions=1,
        diff="reviewed diff",
    )
    diff_sha256 = hashlib.sha256(
        f"{change.path}:{change.after_sha256}".encode()
    ).hexdigest()
    verification = VerificationReport(
        baseline_issue_count=1,
        fixed_issue_count=1,
        current_issue_count=0,
        fixed_issue_ids=["html-has-lang"],
        passed=verification_passed,
        summary="verified",
        diff_sha256=diff_sha256,
    )
    request = AccessPilotRequest(
        target=str(root),
        branch_name="accessibility/accesspilot-fixes",
        execute_pull_request=True,
    )
    remote_url = str(root.parent / "remote.git")
    state = WorkflowState(
        request=request,
        target=ResolvedTarget(
            kind=TargetKind.LOCAL_REPOSITORY,
            original=str(root),
            source_path=str(root),
            remote_url=remote_url,
            default_branch="main",
            source_available=True,
            isolated_copy=True,
        ),
        workspace_path=str(root),
        issues=[
            AccessibilityIssue(
                id="html-has-lang",
                rule_id="html-has-lang",
                title="Document language",
                severity=Severity.SERIOUS,
                wcag_references=["3.1.1"],
                page_url="https://example.test/account",
                route="/account",
                selector="html",
                explanation="The page language was missing.",
                suggested_fix="Add a valid lang attribute.",
            )
        ],
        changes=[change],
        verification=verification,
        pr_draft=PullRequestDraft(
            title="Verified accessibility repair",
            body="Reviewed body",
            branch_name=request.branch_name,
            base_branch="main",
            commit_message="fix(a11y): verified repair",
            draft=True,
            remote_url=remote_url,
        ),
    )
    approval = ApprovalRecord(
        decision=ApprovalDecision.APPROVE,
        diff_sha256=diff_sha256,
        approved_head=_git(root, "rev-parse", "HEAD"),
        verification_fingerprint=verification_fingerprint(verification),
        publication_fingerprint=GitService().publication_fingerprint(state),
    )
    return state, approval


def test_pr_draft_lists_changed_files_and_affected_routes(tmp_path: Path) -> None:
    root, _ = _prepare_repository(tmp_path)
    state, _ = _reviewed_run(root)

    draft = GitService().build_pr_draft(state)

    assert "Changed components/files:" in draft.body
    assert "- `index.html`" in draft.body
    assert "Affected pages/routes:" in draft.body
    assert "- `/account`" in draft.body


def test_rejects_file_changed_after_review(tmp_path: Path) -> None:
    root, _ = _prepare_repository(tmp_path)
    state, approval = _reviewed_run(root)
    (root / "index.html").write_text("tampered after approval\n", encoding="utf-8")

    with pytest.raises(ApprovalRequired, match="changed after verification"):
        GitService().execute_pull_request(state, approval)


def test_rejects_extra_staged_file(tmp_path: Path) -> None:
    root, _ = _prepare_repository(tmp_path)
    state, approval = _reviewed_run(root)
    (root / "notes.txt").write_text(
        "not part of the reviewed patch\n", encoding="utf-8"
    )
    _git(root, "add", "notes.txt")

    with pytest.raises(ApprovalRequired, match="outside the approved file set"):
        GitService().execute_pull_request(state, approval)


def test_rejects_failed_verification(tmp_path: Path) -> None:
    root, _ = _prepare_repository(tmp_path)
    state, approval = _reviewed_run(root, verification_passed=False)

    with pytest.raises(ApprovalRequired, match="passing verification"):
        GitService().execute_pull_request(state, approval)


def test_publication_suppresses_commit_and_push_hooks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, remote = _prepare_repository(tmp_path)
    state, approval = _reviewed_run(root)
    commit_marker = tmp_path / "commit-hook-ran"
    push_marker = tmp_path / "push-hook-ran"
    hooks = root / ".git" / "hooks"
    for name, marker in (
        ("pre-commit", commit_marker),
        ("pre-push", push_marker),
    ):
        hook = hooks / name
        hook.write_text(
            f"#!/bin/sh\nprintf ran > {shlex.quote(str(marker))}\nexit 97\n",
            encoding="utf-8",
        )
        hook.chmod(0o700)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = pr ] && [ "$2" = view ]; then\n'
        "  printf '%s\\n' '{\"url\":\"https://example.test/pull/1\"}'\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_gh.chmod(0o700)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GH_TOKEN", "test-token-must-not-reach-local-git")

    url = GitService().execute_pull_request(state, approval)

    assert url == "https://example.test/pull/1"
    assert not commit_marker.exists()
    assert not push_marker.exists()
    assert _git(root, "log", "-1", "--pretty=%s") == ("fix(a11y): verified repair")
    assert _git(
        remote,
        "rev-parse",
        "refs/heads/accessibility/accesspilot-fixes",
    )
