"""Deterministic issue comparison and project quality checks."""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.schemas import (
    AccessibilityIssue,
    CommandResult,
    FileChange,
    FixStatus,
    FrameworkInfo,
    Severity,
    VerificationReport,
)

from .process import ProcessSupervisor, process_supervisor


class VerificationService:
    def __init__(self, supervisor: ProcessSupervisor | None = None) -> None:
        self.supervisor = supervisor or process_supervisor

    async def run_checks(
        self,
        workspace: Path | None,
        framework: FrameworkInfo | None,
        *,
        allow_execution: bool,
        allow_install: bool,
    ) -> list[CommandResult]:
        if not workspace or not framework or not framework.suggested_test_commands:
            return []
        if not allow_execution:
            return [
                CommandResult(
                    argv=command,
                    cwd=str(workspace),
                    skipped_reason="Local command execution was not authorized.",
                )
                for command in framework.suggested_test_commands
            ]
        results: list[CommandResult] = []
        for command in framework.suggested_test_commands[:4]:
            results.append(
                await self.supervisor.run(
                    command,
                    cwd=workspace,
                    allow_install=allow_install,
                )
            )
        return results

    def compare(
        self,
        baseline: list[AccessibilityIssue],
        current: list[AccessibilityIssue],
        changes: list[FileChange],
        checks: list[CommandResult],
        *,
        scan_evidence_valid: bool,
        scan_errors: list[str] | None = None,
    ) -> VerificationReport:
        baseline_by_id = {issue.id: issue for issue in baseline}
        current_by_id = {issue.id: issue for issue in current}
        fixed_ids = sorted(baseline_by_id.keys() - current_by_id.keys())
        remaining_ids = sorted(baseline_by_id.keys() & current_by_id.keys())
        regression_ids = sorted(current_by_id.keys() - baseline_by_id.keys())
        for issue_id in fixed_ids:
            baseline_by_id[issue_id].status = FixStatus.FIXED
        for issue_id in remaining_ids:
            if baseline_by_id[issue_id].status != FixStatus.UNRESOLVED:
                baseline_by_id[issue_id].status = FixStatus.OPEN
        for issue_id in regression_ids:
            current_by_id[issue_id].status = FixStatus.REGRESSION
        serious_regression = any(
            current_by_id[issue_id].severity in {Severity.CRITICAL, Severity.SERIOUS}
            for issue_id in regression_ids
        )
        checks_pass = all(
            result.skipped_reason is not None or result.exit_code == 0
            for result in checks
        )
        improved = len(current) < len(baseline) if baseline else not current
        scan_errors = list(scan_errors or [])
        passed = (
            scan_evidence_valid
            and not scan_errors
            and improved
            and not serious_regression
            and checks_pass
        )
        return VerificationReport(
            baseline_issue_count=len(baseline),
            fixed_issue_count=len(fixed_ids),
            remaining_issue_count=len(remaining_ids),
            current_issue_count=len(current),
            fixed_issue_ids=fixed_ids,
            remaining_issue_ids=remaining_ids,
            regression_issue_ids=regression_ids,
            scan_evidence_valid=scan_evidence_valid,
            scan_errors=scan_errors,
            checks=checks,
            passed=passed,
            summary=(
                f"Accessibility findings changed from {len(baseline)} to {len(current)}; "
                f"{len(fixed_ids)} fixed, {len(remaining_ids)} remaining, "
                f"{len(regression_ids)} new; "
                f"scan evidence {'valid' if scan_evidence_valid and not scan_errors else 'invalid'}."
            ),
            diff_sha256=_diff_fingerprint(changes),
        )


def verification_fingerprint(report: VerificationReport) -> str:
    payload = "|".join(
        [
            report.diff_sha256,
            *report.fixed_issue_ids,
            "remaining",
            *report.remaining_issue_ids,
            "regressions",
            *report.regression_issue_ids,
            "scan-evidence",
            str(report.scan_evidence_valid),
            *report.scan_errors,
            str(report.passed),
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _diff_fingerprint(changes: list[FileChange]) -> str:
    payload = "\n".join(
        f"{change.path}:{change.after_sha256}"
        for change in sorted(changes, key=lambda item: item.path)
    )
    return hashlib.sha256(payload.encode()).hexdigest()


verification_service = VerificationService()
