"""Structured outputs produced by AccessPilot specialist agents."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["critical", "serious", "moderate", "minor", "unknown"]
RiskLevel = Literal["low", "medium", "high"]


class _SpecialistOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuditedIssue(_SpecialistOutput):
    issue_id: str
    title: str
    severity: Severity
    wcag_references: list[str]
    affected_user_experience: str
    explanation: str
    suggested_fix_strategy: str
    confidence: float = Field(ge=0, le=1)
    evidence_sufficient: bool


class AuditorOutput(_SpecialistOutput):
    issues: list[AuditedIssue]
    caveats: list[str]


class SourceCandidate(_SpecialistOutput):
    path: str
    symbol_or_region: str
    evidence: str
    confidence: float = Field(ge=0, le=1)


class IssueSourceMapping(_SpecialistOutput):
    issue_id: str
    candidates: list[SourceCandidate]
    change_types: list[
        Literal["html", "jsx", "tsx", "css", "aria", "routing", "test", "other"]
    ]
    unresolved_reason: str


class InvestigatorOutput(_SpecialistOutput):
    mappings: list[IssueSourceMapping]
    caveats: list[str]


class PlannedChangeGroup(_SpecialistOutput):
    group_id: str
    issue_ids: list[str]
    files: list[str]
    strategy: str
    test_updates: list[str]
    risk: RiskLevel
    dependencies: list[str]


class PlannerOutput(_SpecialistOutput):
    change_groups: list[PlannedChangeGroup]
    execution_order: list[str]
    unresolved_issue_ids: list[str]
    stop_reason: str


class ProposedEdit(_SpecialistOutput):
    issue_ids: list[str]
    path: str
    change_summary: str
    unified_diff: str
    test_changes: list[str]
    risk_notes: list[str]


class RepairerOutput(_SpecialistOutput):
    edits: list[ProposedEdit]
    skipped_issue_ids: list[str]
    warnings: list[str]


class CheckAssessment(_SpecialistOutput):
    name: str
    status: Literal["passed", "failed", "skipped"]
    evidence: str


class VerifierOutput(_SpecialistOutput):
    resolved_issue_ids: list[str]
    remaining_issue_ids: list[str]
    regression_issue_ids: list[str]
    checks: list[CheckAssessment]
    verdict: Literal["pass", "repair_again", "stop"]
    summary: str


class PullRequestDraft(_SpecialistOutput):
    branch_name: str
    commit_message: str
    title: str
    body: str
    wcag_references: list[str]
    tests_run: list[str]
    remaining_issues: list[str]
    ready_for_human_review: bool
    warnings: list[str]


__all__ = [
    "AuditedIssue",
    "AuditorOutput",
    "CheckAssessment",
    "InvestigatorOutput",
    "IssueSourceMapping",
    "PlannedChangeGroup",
    "PlannerOutput",
    "ProposedEdit",
    "PullRequestDraft",
    "RepairerOutput",
    "SourceCandidate",
    "VerifierOutput",
]
