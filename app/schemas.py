"""Typed contracts shared by AccessPilot's graph, tools, API, and UI."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


def utc_now() -> datetime:
    """Return a timezone-aware timestamp for persisted run metadata."""
    return datetime.now(UTC)


class TargetKind(StrEnum):
    WEBSITE = "website"
    LOCAL_REPOSITORY = "local_repository"
    GITHUB_REPOSITORY = "github_repository"


class FrameworkKind(StrEnum):
    REACT = "react"
    NEXTJS = "nextjs"
    ANGULAR = "angular"
    VUE = "vue"
    SVELTE = "svelte"
    PLAIN_HTML = "plain_html"
    DJANGO = "django"
    FLASK = "flask"
    RAILS = "rails"
    SERVER_RENDERED = "server_rendered"
    UNKNOWN = "unknown"


class LlmProviderName(StrEnum):
    GEMINI = "gemini"
    OPENAI = "openai"


class WcagLevel(StrEnum):
    A = "A"
    AA = "AA"
    AAA = "AAA"


class Severity(StrEnum):
    CRITICAL = "critical"
    SERIOUS = "serious"
    MODERATE = "moderate"
    MINOR = "minor"
    UNKNOWN = "unknown"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    REVISE = "revise"


class FixStatus(StrEnum):
    OPEN = "open"
    PLANNED = "planned"
    FIXED = "fixed"
    UNRESOLVED = "unresolved"
    REGRESSION = "regression"


class AccessPilotRequest(BaseModel):
    """Validated intake accepted by both the graph and REST API."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    target: str = Field(min_length=1, max_length=2048)
    branch_name: str = "accessibility/accesspilot-fixes"
    routes: list[str] = Field(default_factory=list, max_length=100)
    scan_depth: int = Field(default=1, ge=0, le=5)
    wcag_level: WcagLevel = WcagLevel.AA
    llm_provider: LlmProviderName = LlmProviderName.GEMINI
    authentication_instructions: str | None = Field(default=None, max_length=4000)
    start_command: list[str] | None = Field(default=None, max_length=20)
    app_url: str | None = Field(default=None, max_length=2048)
    allow_local_execution: bool = False
    allow_dependency_install: bool = False
    max_repair_rounds: int = Field(default=3, ge=1, le=5)
    execute_pull_request: bool = False
    draft_pull_request: bool = True
    demo_mode: bool = False

    @field_validator("branch_name")
    @classmethod
    def validate_branch_name(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,119}", value):
            raise ValueError("branch name contains unsupported characters")
        if ".." in value or value.endswith(("/", ".lock")):
            raise ValueError("branch name is not safe")
        return value

    @field_validator("routes")
    @classmethod
    def normalize_routes(cls, routes: list[str]) -> list[str]:
        normalized: list[str] = []
        for route in routes:
            route = route.strip()
            if not route:
                continue
            if not route.startswith(("/", "http://", "https://")):
                route = f"/{route}"
            if route not in normalized:
                normalized.append(route)
        return normalized

    @field_validator("start_command")
    @classmethod
    def validate_start_command(cls, command: list[str] | None) -> list[str] | None:
        if command is None:
            return None
        if not command or any(not part.strip() for part in command):
            raise ValueError("start_command must contain non-empty argv values")
        return command

    @field_validator("authentication_instructions")
    @classmethod
    def reject_secret_auth_material(cls, value: str | None) -> str | None:
        if value is None:
            return None
        secret_patterns = (
            r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}",
            r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",
            r"(?i)(?:api[_-]?key|token|secret|password|cookie)\s*[:=]\s*\S+",
        )
        if any(re.search(pattern, value) for pattern in secret_patterns):
            raise ValueError(
                "authentication_instructions must describe a process, not contain credentials"
            )
        return value


class ResolvedTarget(BaseModel):
    kind: TargetKind
    original: str
    normalized_url: str | None = None
    source_path: str | None = None
    remote_url: str | None = None
    default_branch: str | None = None
    source_available: bool = False
    isolated_copy: bool = False


class FrameworkInfo(BaseModel):
    kind: FrameworkKind = FrameworkKind.UNKNOWN
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    manifests: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    suggested_start_command: list[str] | None = None
    suggested_build_command: list[str] | None = None
    suggested_test_commands: list[list[str]] = Field(default_factory=list)


class PageTarget(BaseModel):
    route: str
    url: str
    source_hint: str | None = None


class AxeNode(BaseModel):
    html: str = ""
    target: list[str] = Field(default_factory=list)
    failure_summary: str | None = None
    impact: str | None = None


class AxeViolation(BaseModel):
    id: str
    impact: str | None = None
    tags: list[str] = Field(default_factory=list)
    description: str = ""
    help: str = ""
    help_url: str | None = None
    nodes: list[AxeNode] = Field(default_factory=list)


class PageScanResult(BaseModel):
    url: str
    route: str
    title: str | None = None
    scanner: str = "axe-core"
    scanner_version: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    duration_ms: int = 0
    violations: list[AxeViolation] = Field(default_factory=list)
    passes: int = 0
    incomplete: int = 0
    inapplicable: int = 0
    screenshot_path: str | None = None
    error: str | None = None


class SourceLocation(BaseModel):
    path: str
    line: int | None = Field(default=None, ge=1)
    column: int | None = Field(default=None, ge=1)
    snippet: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class AccessibilityIssue(BaseModel):
    id: str
    rule_id: str
    title: str
    severity: Severity
    wcag_references: list[str] = Field(default_factory=list)
    page_url: str
    route: str
    selector: str
    screenshot_path: str | None = None
    dom_snippet: str | None = None
    explanation: str
    suggested_fix: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    help_url: str | None = None
    tags: list[str] = Field(default_factory=list)
    source_locations: list[SourceLocation] = Field(default_factory=list)
    status: FixStatus = FixStatus.OPEN


class SourceMapping(BaseModel):
    issue_id: str
    candidates: list[SourceLocation] = Field(default_factory=list)
    selected_path: str | None = None
    rationale: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class RepairAction(BaseModel):
    id: str
    issue_ids: list[str]
    file_path: str
    strategy: str
    rationale: str
    change_kind: Literal[
        "html", "jsx", "tsx", "css", "aria", "routing", "test", "config"
    ]
    risk: RiskLevel = RiskLevel.LOW
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    expected_result: str
    automatic: bool = True


class RepairPlan(BaseModel):
    summary: str = ""
    actions: list[RepairAction] = Field(default_factory=list)
    unresolved_issue_ids: list[str] = Field(default_factory=list)
    overall_risk: RiskLevel = RiskLevel.LOW
    generated_by: str = "deterministic"


class FileChange(BaseModel):
    path: str
    issue_ids: list[str] = Field(default_factory=list)
    summary: str
    before_sha256: str
    after_sha256: str
    additions: int = 0
    deletions: int = 0
    diff: str = ""


class PatchProposal(BaseModel):
    issue_ids: list[str] = Field(default_factory=list)
    path: str = Field(max_length=500)
    change_summary: str = Field(max_length=2000)
    unified_diff: str = Field(max_length=200_000)
    test_changes: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)


class CommandResult(BaseModel):
    argv: list[str]
    cwd: str
    exit_code: int | None = None
    duration_ms: int = 0
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    skipped_reason: str | None = None


class PreviewInfo(BaseModel):
    original_url: str | None = None
    fixed_url: str | None = None
    original_screenshot: str | None = None
    fixed_screenshot: str | None = None


class VerificationReport(BaseModel):
    baseline_issue_count: int = 0
    fixed_issue_count: int = 0
    remaining_issue_count: int = 0
    current_issue_count: int = 0
    fixed_issue_ids: list[str] = Field(default_factory=list)
    remaining_issue_ids: list[str] = Field(default_factory=list)
    regression_issue_ids: list[str] = Field(default_factory=list)
    scan_evidence_valid: bool = False
    scan_errors: list[str] = Field(default_factory=list)
    checks: list[CommandResult] = Field(default_factory=list)
    passed: bool = False
    summary: str = ""
    diff_sha256: str = ""


class PullRequestDraft(BaseModel):
    title: str
    body: str
    branch_name: str
    base_branch: str | None = None
    commit_message: str
    draft: bool = True
    remote_url: str | None = None


class ApprovalRecord(BaseModel):
    decision: ApprovalDecision
    diff_sha256: str
    approved_head: str | None = None
    verification_fingerprint: str
    publication_fingerprint: str
    decided_at: datetime = Field(default_factory=utc_now)
    reviewer_note: str | None = None


class ApprovalRequest(BaseModel):
    decision: ApprovalDecision
    expected_diff_sha256: str
    reviewer_note: str | None = Field(default=None, max_length=2000)


class WorkflowMessage(BaseModel):
    level: Literal["info", "warning", "error"] = "info"
    stage: str
    text: str
    at: datetime = Field(default_factory=utc_now)


class WorkflowState(BaseModel):
    """Serializable state passed between graph nodes and exposed by the API."""

    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    request: AccessPilotRequest
    status: RunStatus = RunStatus.QUEUED
    stage: str = "intake"
    target: ResolvedTarget | None = None
    workspace_path: str | None = None
    framework: FrameworkInfo | None = None
    repository_guidance: list[str] = Field(default_factory=list)
    app_url: str | None = None
    pages: list[PageTarget] = Field(default_factory=list)
    baseline_scans: list[PageScanResult] = Field(default_factory=list)
    verification_scans: list[PageScanResult] = Field(default_factory=list)
    issues: list[AccessibilityIssue] = Field(default_factory=list)
    verification_issues: list[AccessibilityIssue] = Field(default_factory=list)
    mappings: list[SourceMapping] = Field(default_factory=list)
    repair_plan: RepairPlan | None = None
    patch_proposals: list[PatchProposal] = Field(default_factory=list)
    changes: list[FileChange] = Field(default_factory=list)
    preview: PreviewInfo = Field(default_factory=PreviewInfo)
    verification: VerificationReport | None = None
    pr_draft: PullRequestDraft | None = None
    pull_request_url: str | None = None
    approval: ApprovalRecord | None = None
    revision_feedback: str | None = None
    repair_round: int = 0
    issue_fingerprints: list[str] = Field(default_factory=list)
    diff_fingerprints: list[str] = Field(default_factory=list)
    unresolved_issue_ids: list[str] = Field(default_factory=list)
    messages: list[WorkflowMessage] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    def workspace(self) -> Path | None:
        return Path(self.workspace_path) if self.workspace_path else None


class RunEvent(BaseModel):
    sequence: int
    run_id: str
    stage: str
    status: RunStatus
    message: str
    at: datetime = Field(default_factory=utc_now)
    payload: dict[str, Any] = Field(default_factory=dict)


class RunAccepted(BaseModel):
    run_id: str
    status: RunStatus
    poll_url: str


class RunSnapshot(BaseModel):
    state: WorkflowState
    events: list[RunEvent] = Field(default_factory=list)


class ProviderAvailability(BaseModel):
    name: LlmProviderName
    configured: bool
    model: str
    detail: str


class PublicConfig(BaseModel):
    providers: list[ProviderAvailability]
    default_provider: LlmProviderName
    max_repair_rounds: int
    sample_target: str
    local_execution_enabled: bool = False
    dependency_install_enabled: bool = False
    remote_mutations_enabled: bool = False


class FinalReport(BaseModel):
    run_id: str
    status: RunStatus
    target: str
    framework: FrameworkInfo | None
    original_issues: list[AccessibilityIssue]
    applied_changes: list[FileChange]
    remaining_issue_ids: list[str]
    verification: VerificationReport | None
    preview: PreviewInfo
    pr_draft: PullRequestDraft | None
    approval: ApprovalRecord | None
    limitations: list[str]


class UrlReference(BaseModel):
    """Small strict URL wrapper used by preview/artifact endpoints."""

    url: HttpUrl
