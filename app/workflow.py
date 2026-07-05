"""AccessPilot's ADK 2 graph workflow and reusable node implementations."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from google.adk.agents.context import Context
from google.adk.events import Event, EventActions, RequestInput
from google.adk.workflow import FunctionNode, RetryConfig, Workflow
from google.genai import types

from app.providers import LLMConfig, create_llm_model
from app.schemas import (
    AccessibilityIssue,
    AccessPilotRequest,
    ApprovalDecision,
    ApprovalRecord,
    FinalReport,
    FixStatus,
    PatchProposal,
    PreviewInfo,
    PullRequestDraft,
    RepairAction,
    RepairPlan,
    RiskLevel,
    RunStatus,
    SourceLocation,
    WorkflowMessage,
    WorkflowState,
    utc_now,
)
from app.services.app_runner import local_app_runner
from app.services.framework import detect_framework
from app.services.git_service import git_service
from app.services.guidance import load_repository_guidance
from app.services.mapper import source_mapper
from app.services.normalize import issue_set_fingerprint, normalize_scan_results
from app.services.previews import preview_service
from app.services.repair import changes_fingerprint, repair_engine
from app.services.routes import discover_routes
from app.services.safety import redact_text, safe_path, validate_editable_path
from app.services.scanner import accessibility_scanner
from app.services.verification import verification_fingerprint, verification_service
from app.services.workspace import workspace_manager
from app.settings import get_settings
from app.specialists import create_specialist_agents
from app.specialists.schemas import (
    AuditedIssue,
    AuditorOutput,
    CheckAssessment,
    InvestigatorOutput,
    IssueSourceMapping,
    PlannedChangeGroup,
    PlannerOutput,
    RepairerOutput,
    SourceCandidate,
    VerifierOutput,
)
from app.specialists.schemas import (
    PullRequestDraft as SpecialistPullRequestDraft,
)


def _coerce_state(value: WorkflowState | dict[str, Any]) -> WorkflowState:
    return (
        value
        if isinstance(value, WorkflowState)
        else WorkflowState.model_validate(value)
    )


def _is_managed_loopback_app(state: WorkflowState) -> bool:
    """Return whether this run started the resolved loopback app itself."""
    if not (
        state.app_url
        and state.workspace()
        and state.request.allow_local_execution
        and state.request.app_url is None
    ):
        return False
    parsed = urlsplit(state.app_url)
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "::1"}


def _is_trusted_crawl_target(state: WorkflowState) -> bool:
    return (
        state.request.demo_mode
        or state.request.target == "sample://vulnerable-app"
        or _is_managed_loopback_app(state)
    )


def _allow_private_scan(state: WorkflowState) -> bool:
    return (
        _is_trusted_crawl_target(state) or get_settings().allow_private_network_targets
    )


def _state_from_context(ctx: Context) -> WorkflowState:
    value = ctx.state.get("workflow_state")
    if not value:
        raise RuntimeError("Workflow state is unavailable")
    return _coerce_state(value)


def _event(
    state: WorkflowState,
    *,
    stage: str,
    message: str,
    route: str | None = None,
    status: RunStatus | None = None,
) -> Event:
    state.stage = stage
    state.status = status or state.status
    state.updated_at = utc_now()
    state.messages.append(WorkflowMessage(stage=stage, text=message))
    data = state.model_dump(mode="json")
    actions = EventActions(route=route)
    actions.state_delta["workflow_state"] = data
    return Event(output=data, actions=actions)


def _state_event_only(state: WorkflowState, *, stage: str, message: str) -> Event:
    state.stage = stage
    state.updated_at = utc_now()
    state.messages.append(WorkflowMessage(stage=stage, text=message))
    actions = EventActions()
    actions.state_delta["workflow_state"] = state.model_dump(mode="json")
    return Event(actions=actions)


def intake_node(ctx: Context, node_input: AccessPilotRequest | dict[str, Any]) -> Event:
    request = (
        node_input
        if isinstance(node_input, AccessPilotRequest)
        else AccessPilotRequest.model_validate(node_input)
    )
    settings = get_settings()
    if request.allow_local_execution and not settings.enable_local_execution:
        raise ValueError("Local application execution is disabled by server policy")
    if request.allow_dependency_install and not settings.enable_dependency_install:
        raise ValueError("Dependency installation is disabled by server policy")
    if request.execute_pull_request and not settings.enable_remote_mutations:
        raise ValueError("Remote repository mutation is disabled by server policy")
    if request.max_repair_rounds > settings.max_repair_rounds:
        raise ValueError("Requested repair rounds exceed the server policy limit")
    state = WorkflowState(
        run_id=ctx.session.id,
        request=request,
        status=RunStatus.RUNNING,
    )
    return _event(
        state,
        stage="intake",
        message="Input validated; beginning an isolated accessibility run.",
    )


async def repository_loader_node(node_input: dict[str, Any]) -> Event:
    state = _coerce_state(node_input)
    target, workspace = await workspace_manager.prepare(state.request, state.run_id)
    state.target = target
    state.workspace_path = str(workspace) if workspace else None
    return _event(
        state,
        stage="repository_loader",
        message=(
            "Created an isolated source copy."
            if workspace
            else "No source was provided; this run will remain scan-only."
        ),
    )


def framework_detector_node(node_input: dict[str, Any]) -> Event:
    state = _coerce_state(node_input)
    state.framework = detect_framework(state.workspace()) if state.workspace() else None
    state.repository_guidance = load_repository_guidance(state.workspace())
    detail = state.framework.kind.value if state.framework else "remote URL"
    return _event(
        state,
        stage="framework_detector",
        message=(
            f"Framework detection completed: {detail}; "
            f"loaded {len(state.repository_guidance)} repository guidance file(s)."
        ),
    )


async def local_app_runner_node(node_input: dict[str, Any]) -> Event:
    state = _coerce_state(node_input)
    if not state.target:
        raise RuntimeError("Repository loader did not resolve a target")
    state.app_url = await local_app_runner.resolve_or_start(
        run_id=state.run_id,
        request=state.request,
        target=state.target,
        framework=state.framework,
        workspace=state.workspace(),
    )
    if state.app_url:
        return _event(
            state,
            stage="local_app_runner",
            message=f"Rendered application is available at {state.app_url}.",
            route="scan",
        )
    return _event(
        state,
        stage="local_app_runner",
        message="The application could not be rendered; producing a source-only report.",
        route="report",
    )


def route_discovery_node(node_input: dict[str, Any]) -> Event:
    state = _coerce_state(node_input)
    if not state.app_url:
        return _event(
            state,
            stage="route_discovery",
            message="No rendered URL is available.",
            route="report",
        )
    state.pages = discover_routes(
        state.request,
        state.framework,
        state.workspace(),
        state.app_url,
        limit=get_settings().max_routes,
        allow_network_crawl=_is_trusted_crawl_target(state),
    )
    return _event(
        state,
        stage="route_discovery",
        message=f"Discovered {len(state.pages)} route(s) for scanning.",
    )


async def accessibility_scan_node(node_input: dict[str, Any]) -> Event:
    state = _coerce_state(node_input)
    state.baseline_scans = await accessibility_scanner.scan(
        state.pages,
        run_id=state.run_id,
        phase="baseline",
        workspace=state.workspace(),
        wcag_level=state.request.wcag_level,
        allow_private_network=_allow_private_scan(state),
    )
    scan_errors = [scan.error for scan in state.baseline_scans if scan.error]
    if len(state.baseline_scans) != len(state.pages) or scan_errors:
        raise RuntimeError(
            f"Baseline evidence failed for {len(scan_errors) or 1} route(s)."
        )
    return _event(
        state,
        stage="accessibility_scan",
        message=f"Baseline scanning completed for {len(state.baseline_scans)} page(s).",
    )


def issue_normalization_node(node_input: dict[str, Any]) -> Event:
    state = _coerce_state(node_input)
    state.issues = normalize_scan_results(state.baseline_scans)
    fingerprint = issue_set_fingerprint(state.issues)
    state.issue_fingerprints.append(fingerprint)
    return _event(
        state,
        stage="issue_normalization",
        message=f"Normalized {len(state.issues)} distinct accessibility issue(s).",
    )


def auditor_input_node(node_input: dict[str, Any]) -> dict[str, Any]:
    state = _coerce_state(node_input)
    return {
        "wcag_level": state.request.wcag_level.value,
        "issues": [
            _issue_for_specialist(
                issue,
                include={
                    "id",
                    "rule_id",
                    "title",
                    "severity",
                    "wcag_references",
                    "page_url",
                    "route",
                    "selector",
                    "dom_snippet",
                    "explanation",
                    "suggested_fix",
                    "confidence",
                    "tags",
                },
            )
            for issue in state.issues
        ],
    }


def investigator_input_node(node_input: dict[str, Any]) -> dict[str, Any]:
    state = _coerce_state(node_input)
    return {
        "framework": state.framework.model_dump(mode="json")
        if state.framework
        else None,
        "issues": [
            _issue_for_specialist(
                issue,
                include={"id", "rule_id", "route", "selector", "dom_snippet"},
            )
            for issue in (state.verification_issues or state.issues)
        ],
        "deterministic_mappings": [
            _mapping_for_specialist(mapping) for mapping in state.mappings
        ],
        "repository_guidance": state.repository_guidance,
    }


def planner_input_node(node_input: dict[str, Any]) -> dict[str, Any]:
    state = _coerce_state(node_input)
    return {
        "framework": state.framework.model_dump(mode="json")
        if state.framework
        else None,
        "issues": [
            _issue_for_specialist(issue)
            for issue in (state.verification_issues or state.issues)
        ],
        "mappings": [_mapping_for_specialist(mapping) for mapping in state.mappings],
        "repository_guidance": state.repository_guidance,
        "repair_round": state.repair_round,
        "max_repair_rounds": state.request.max_repair_rounds,
        "reviewer_note": state.revision_feedback,
    }


def _issue_for_specialist(
    issue: AccessibilityIssue,
    *,
    include: set[str] | None = None,
) -> dict[str, Any]:
    data = issue.model_dump(mode="json", include=include)
    for key in ("dom_snippet", "explanation", "suggested_fix"):
        if isinstance(data.get(key), str):
            data[key] = redact_text(data[key], limit=4_000)
    return data


def _mapping_for_specialist(mapping) -> dict[str, Any]:
    data = mapping.model_dump(mode="json")
    for candidate in data.get("candidates", []):
        snippet = candidate.get("snippet")
        if isinstance(snippet, str):
            candidate["snippet"] = redact_text(snippet, limit=1_000)
    return data


def repairer_input_node(node_input: dict[str, Any]) -> dict[str, Any]:
    state = _coerce_state(node_input)
    workspace = state.workspace()
    source_files: list[dict[str, str]] = []
    remaining = 120_000
    if workspace and state.repair_plan:
        for relative in dict.fromkeys(
            action.file_path for action in state.repair_plan.actions
        ):
            if remaining <= 0:
                break
            try:
                path = validate_editable_path(workspace, relative)
                text = path.read_text(encoding="utf-8")[: min(40_000, remaining)]
            except (OSError, UnicodeDecodeError, RuntimeError, ValueError):
                continue
            text = redact_text(text)
            source_files.append({"path": relative, "content": text})
            remaining -= len(text)
    return {
        "repair_plan": state.repair_plan.model_dump(mode="json")
        if state.repair_plan
        else None,
        "source_files": source_files,
        "repository_guidance": state.repository_guidance,
    }


def verifier_input_node(node_input: dict[str, Any]) -> dict[str, Any]:
    state = _coerce_state(node_input)
    return {
        "verification": state.verification.model_dump(mode="json")
        if state.verification
        else None,
        "changes": [
            {
                **change.model_dump(mode="json", exclude={"diff"}),
                "diff": redact_text(change.diff, limit=20_000),
            }
            for change in state.changes
        ],
    }


def pr_writer_input_node(node_input: dict[str, Any]) -> dict[str, Any]:
    state = _coerce_state(node_input)
    return {
        "branch_name": state.request.branch_name,
        "draft": state.request.draft_pull_request,
        "issues": [
            issue.model_dump(
                mode="json",
                include={"id", "title", "wcag_references", "route", "status"},
            )
            for issue in state.issues
        ],
        "changes": [
            change.model_dump(mode="json", exclude={"diff"}) for change in state.changes
        ],
        "verification": state.verification.model_dump(mode="json")
        if state.verification
        else None,
    }


def source_mapping_node(node_input: dict[str, Any]) -> Event:
    state = _coerce_state(node_input)
    current = state.verification_issues or state.issues
    if state.workspace():
        state.mappings = source_mapper.map_issues(state.workspace(), current)
    else:
        state.mappings = []
    return _event(
        state,
        stage="source_mapping",
        message=f"Mapped {sum(bool(mapping.selected_path) for mapping in state.mappings)} issue(s) to source candidates.",
    )


def merge_auditor_node(ctx: Context, node_input: dict[str, Any]) -> Event:
    state = _state_from_context(ctx)
    output = AuditorOutput.model_validate(node_input)
    by_id = {issue.id: issue for issue in state.issues}
    for audited in output.issues:
        issue = by_id.get(audited.issue_id)
        if not issue:
            continue
        issue.title = audited.title
        issue.explanation = audited.explanation
        issue.suggested_fix = audited.suggested_fix_strategy
        issue.confidence = min(issue.confidence, audited.confidence)
    for caveat in output.caveats:
        state.messages.append(
            WorkflowMessage(level="warning", stage="accessibility_auditor", text=caveat)
        )
    return _event(
        state,
        stage="accessibility_auditor",
        message="Accessibility Auditor reviewed the deterministic findings.",
    )


def merge_investigator_node(ctx: Context, node_input: dict[str, Any]) -> Event:
    state = _state_from_context(ctx)
    output = InvestigatorOutput.model_validate(node_input)
    workspace = state.workspace()
    mapping_by_id = {mapping.issue_id: mapping for mapping in state.mappings}
    if workspace:
        for proposed in output.mappings:
            mapping = mapping_by_id.get(proposed.issue_id)
            if not mapping:
                continue
            for candidate in proposed.candidates:
                if candidate.confidence < 0.6:
                    continue
                try:
                    path = safe_path(workspace, candidate.path, must_exist=True)
                except Exception:
                    continue
                if not path.is_file():
                    continue
                relative = path.relative_to(workspace).as_posix()
                if any(item.path == relative for item in mapping.candidates):
                    continue
                mapping.candidates.append(
                    SourceLocation(
                        path=relative,
                        snippet=candidate.evidence[:500],
                        confidence=candidate.confidence,
                    )
                )
            mapping.candidates.sort(key=lambda value: value.confidence, reverse=True)
            if mapping.candidates and mapping.candidates[0].confidence >= 0.6:
                mapping.selected_path = mapping.candidates[0].path
                mapping.confidence = mapping.candidates[0].confidence
            else:
                mapping.selected_path = None
                mapping.confidence = 0.0
    return _event(
        state,
        stage="codebase_investigator",
        message="Codebase Investigator ranked source evidence without expanding the workspace boundary.",
    )


def repairability_router_node(node_input: dict[str, Any]) -> Event:
    state = _coerce_state(node_input)
    if not state.issues:
        return _event(
            state,
            stage="repairability_check",
            message="No scanner findings require repair.",
            route="report",
        )
    if not state.workspace():
        state.unresolved_issue_ids = [issue.id for issue in state.issues]
        return _event(
            state,
            stage="repairability_check",
            message="Source is unavailable, so findings remain report-only.",
            route="report",
        )
    return _event(
        state,
        stage="repairability_check",
        message="Source and mapped findings are available for repair planning.",
        route="repair",
    )


def merge_planner_node(ctx: Context, node_input: dict[str, Any]) -> Event:
    state = _state_from_context(ctx)
    output = PlannerOutput.model_validate(node_input)
    current = state.verification_issues or state.issues
    if not state.workspace():
        raise RuntimeError("Repair planning requires a workspace")
    deterministic_plan = repair_engine.plan(state.workspace(), current, state.mappings)
    state.repair_plan = _reconcile_planner_output(
        deterministic_plan,
        output,
        known_issue_ids={issue.id for issue in current},
        workspace=state.workspace(),
    )
    state.repair_plan.generated_by = "repair-planner-agent + deterministic-policy"
    state.revision_feedback = None
    if output.stop_reason:
        state.messages.append(
            WorkflowMessage(
                level="warning", stage="repair_planner", text=output.stop_reason
            )
        )
        state.repair_plan.actions = []
        state.repair_plan.unresolved_issue_ids = sorted({issue.id for issue in current})
        state.repair_plan.summary = (
            f"Planner stopped safely; {len(current)} issue(s) require human review."
        )
    state.unresolved_issue_ids = sorted(
        set(state.repair_plan.unresolved_issue_ids + output.unresolved_issue_ids)
    )
    unresolved = set(state.unresolved_issue_ids)
    for issue in current:
        if issue.id in unresolved:
            issue.status = FixStatus.UNRESOLVED
    return _event(
        state,
        stage="repair_planner",
        message=state.repair_plan.summary,
    )


def _reconcile_planner_output(
    deterministic: RepairPlan,
    output: PlannerOutput,
    *,
    known_issue_ids: set[str],
    workspace: Path,
) -> RepairPlan:
    """Use agent grouping/order while retaining deterministic path ownership."""
    unresolved = set(deterministic.unresolved_issue_ids)
    unresolved.update(
        issue_id
        for issue_id in output.unresolved_issue_ids
        if issue_id in known_issue_ids
    )
    groups_by_id = {group.group_id: group for group in output.change_groups}
    ordered_ids = [
        group_id for group_id in output.execution_order if group_id in groups_by_id
    ]
    ordered_ids.extend(
        group_id for group_id in groups_by_id if group_id not in ordered_ids
    )
    consumed: set[str] = set()
    approved_paths: set[str] = set()
    actions: list[RepairAction] = []
    for group_id in ordered_ids:
        group = groups_by_id[group_id]
        group_issues = {
            issue_id
            for issue_id in group.issue_ids
            if issue_id in known_issue_ids and issue_id not in unresolved
        }
        candidates = [
            action
            for action in deterministic.actions
            if action.id not in consumed
            and group_issues.intersection(action.issue_ids)
            and (not group.files or action.file_path in group.files)
        ]
        by_file: dict[tuple[str, str], list[RepairAction]] = {}
        for action in candidates:
            by_file.setdefault((action.file_path, action.change_kind), []).append(
                action
            )
        for index, ((file_path, change_kind), grouped) in enumerate(by_file.items()):
            consumed.update(action.id for action in grouped)
            issue_ids = sorted(
                {
                    issue_id
                    for action in grouped
                    for issue_id in action.issue_ids
                    if issue_id not in unresolved
                }
            )
            actions.append(
                RepairAction(
                    id=f"{group_id}-{index + 1}",
                    issue_ids=issue_ids,
                    file_path=file_path,
                    strategy=group.strategy or grouped[0].strategy,
                    rationale=(
                        "Agent-grouped change constrained to deterministic source mappings."
                    ),
                    change_kind=change_kind,
                    risk=RiskLevel(group.risk),
                    confidence=min(action.confidence for action in grouped),
                    expected_result="Resolve the grouped scanner findings without unrelated edits.",
                    automatic=True,
                )
            )
            approved_paths.add(file_path)
        group_issue_ids = sorted(group_issues)
        for relative in dict.fromkeys([*group.files, *group.test_updates]):
            if relative in approved_paths or not group_issue_ids:
                continue
            try:
                path = validate_editable_path(workspace, relative)
            except (OSError, RuntimeError, ValueError):
                continue
            kind = _planner_change_kind(
                path,
                test_update=relative in group.test_updates,
            )
            if kind is None:
                continue
            approved_paths.add(relative)
            actions.append(
                RepairAction(
                    id=f"{group_id}-proposal-{len(actions) + 1}",
                    issue_ids=group_issue_ids,
                    file_path=relative,
                    strategy=group.strategy,
                    rationale=(
                        "Planner-authorized existing file; requires a validated "
                        "repairer unified diff."
                    ),
                    change_kind=kind,
                    risk=RiskLevel(group.risk),
                    confidence=0.5,
                    expected_result="Add the focused related source or test update.",
                    automatic=False,
                )
            )
    actions.extend(
        action
        for action in deterministic.actions
        if action.id not in consumed and not unresolved.intersection(action.issue_ids)
    )
    overall_risk = (
        RiskLevel.HIGH
        if any(action.risk == RiskLevel.HIGH for action in actions)
        else RiskLevel.MEDIUM
        if any(action.risk == RiskLevel.MEDIUM for action in actions)
        else RiskLevel.LOW
    )
    return RepairPlan(
        summary=(
            f"Apply {len(actions)} ordered, policy-constrained repair group(s); "
            f"leave {len(unresolved)} issue(s) for human review."
        ),
        actions=actions,
        unresolved_issue_ids=sorted(unresolved),
        overall_risk=overall_risk,
        generated_by="repair-planner-agent + deterministic-policy",
    )


def _planner_change_kind(path: Path, *, test_update: bool) -> str | None:
    relative_parts = {part.lower() for part in path.parts}
    test_like = test_update or bool(
        relative_parts.intersection({"test", "tests", "__tests__"})
        or ".test." in path.name.lower()
        or ".spec." in path.name.lower()
    )
    if test_like:
        return "test"
    return {
        ".html": "html",
        ".htm": "html",
        ".css": "css",
        ".scss": "css",
        ".jsx": "jsx",
        ".tsx": "tsx",
        ".js": "routing",
        ".ts": "routing",
        ".vue": "html",
        ".svelte": "html",
        ".py": "routing",
    }.get(path.suffix.lower())


def merge_repairer_node(ctx: Context, node_input: dict[str, Any]) -> Event:
    state = _state_from_context(ctx)
    output = RepairerOutput.model_validate(node_input)
    allowed_by_path: dict[str, set[str]] = {}
    for action in state.repair_plan.actions if state.repair_plan else []:
        allowed_by_path.setdefault(action.file_path, set()).update(action.issue_ids)
    proposals: list[PatchProposal] = []
    for edit in output.edits:
        allowed_issue_ids = allowed_by_path.get(edit.path, set())
        if not edit.issue_ids or not set(edit.issue_ids) <= allowed_issue_ids:
            state.messages.append(
                WorkflowMessage(
                    level="warning",
                    stage="code_repair_agent",
                    text=f"Ignored an out-of-plan patch proposal for {edit.path}.",
                )
            )
            continue
        proposals.append(PatchProposal.model_validate(edit.model_dump()))
    state.patch_proposals = proposals
    state.unresolved_issue_ids = sorted(
        set(state.unresolved_issue_ids + output.skipped_issue_ids)
    )
    for warning in output.warnings:
        state.messages.append(
            WorkflowMessage(level="warning", stage="code_repair_agent", text=warning)
        )
    return _event(
        state,
        stage="code_repair_agent",
        message=(
            f"Code Repair Agent proposed {len(output.edits)} patch(es); "
            f"{len(proposals)} passed plan-bound validation."
        ),
    )


def patch_validation_node(node_input: dict[str, Any]) -> Event:
    state = _coerce_state(node_input)
    if not state.workspace() or not state.repair_plan:
        return _event(
            state,
            stage="patch_validation",
            message="No safe repair plan is available.",
            route="report",
        )
    git_service.ensure_local_branch(state.workspace(), state.request.branch_name)
    current = state.verification_issues or state.issues
    new_changes = repair_engine.apply(
        state.workspace(),
        state.repair_plan,
        current,
        state.patch_proposals,
    )
    state.repair_round += 1
    if new_changes:
        by_path = {change.path: change for change in state.changes}
        for change in new_changes:
            previous = by_path.get(change.path)
            if previous:
                change = change.model_copy(
                    update={
                        "issue_ids": sorted(set(previous.issue_ids + change.issue_ids)),
                        "summary": "; ".join(
                            dict.fromkeys(
                                part
                                for part in (
                                    *previous.summary.split("; "),
                                    *change.summary.split("; "),
                                )
                                if part
                            )
                        ),
                        "before_sha256": previous.before_sha256,
                        "additions": previous.additions + change.additions,
                        "deletions": previous.deletions + change.deletions,
                        "diff": f"{previous.diff.rstrip()}\n{change.diff.lstrip()}",
                    }
                )
            by_path[change.path] = change
        state.changes = list(by_path.values())
        fingerprint = changes_fingerprint(state.changes)
        state.diff_fingerprints.append(fingerprint)
        return _event(
            state,
            stage="patch_validation",
            message=f"Applied and hash-validated {len(new_changes)} source file change(s).",
            route="verify",
        )
    return _event(
        state,
        stage="patch_validation",
        message="No supported source edit changed the working copy.",
        route="report",
    )


def _verification_pages(state: WorkflowState):
    workspace = state.workspace()
    if (
        state.request.demo_mode or state.request.target == "sample://vulnerable-app"
    ) and workspace:
        pages = []
        for page in state.pages:
            relative = page.route.lstrip("/") or "index.html"
            candidate = workspace / relative
            if candidate.is_dir():
                candidate /= "index.html"
            if not candidate.exists():
                candidate = workspace / "index.html"
            pages.append(page.model_copy(update={"url": candidate.resolve().as_uri()}))
        return pages
    return state.pages


async def verification_scan_node(node_input: dict[str, Any]) -> Event:
    state = _coerce_state(node_input)
    if (
        state.target
        and state.workspace()
        and state.request.allow_local_execution
        and not (
            state.request.demo_mode or state.request.target == "sample://vulnerable-app"
        )
    ):
        state.app_url = await local_app_runner.resolve_or_start(
            run_id=state.run_id,
            request=state.request,
            target=state.target,
            framework=state.framework,
            workspace=state.workspace(),
        )
        if state.app_url:
            state.pages = discover_routes(
                state.request,
                state.framework,
                state.workspace(),
                state.app_url,
                limit=get_settings().max_routes,
                allow_network_crawl=_is_trusted_crawl_target(state),
            )
    state.verification_scans = await accessibility_scanner.scan(
        _verification_pages(state),
        run_id=state.run_id,
        phase=f"verification-{state.repair_round}",
        workspace=state.workspace(),
        wcag_level=state.request.wcag_level,
        allow_private_network=_allow_private_scan(state),
    )
    state.verification_issues = normalize_scan_results(state.verification_scans)
    checks = await verification_service.run_checks(
        state.workspace(),
        state.framework,
        allow_execution=state.request.allow_local_execution,
        allow_install=state.request.allow_dependency_install,
    )
    state.verification = verification_service.compare(
        state.issues,
        state.verification_issues,
        state.changes,
        checks,
        scan_evidence_valid=(
            len(state.verification_scans) == len(state.pages)
            and all(not scan.error for scan in state.verification_scans)
        ),
        scan_errors=[
            scan.error or "Missing verification result"
            for scan in state.verification_scans
            if scan.error
        ],
    )
    return _event(
        state,
        stage="verification_scan",
        message=state.verification.summary,
    )


def merge_verifier_node(ctx: Context, node_input: dict[str, Any]) -> Event:
    state = _state_from_context(ctx)
    output = VerifierOutput.model_validate(node_input)
    if not state.verification:
        raise RuntimeError("Deterministic verification is unavailable")
    if set(output.regression_issue_ids) - set(state.verification.regression_issue_ids):
        state.messages.append(
            WorkflowMessage(
                level="warning",
                stage="verification_agent",
                text="Verifier suggested regressions that deterministic fingerprints did not confirm.",
            )
        )
    return _event(
        state,
        stage="verification_agent",
        message=f"Verification Agent verdict: {output.verdict}. {output.summary}",
    )


def regression_check_node(node_input: dict[str, Any]) -> Event:
    state = _coerce_state(node_input)
    if not state.verification:
        return _event(
            state,
            stage="regression_check",
            message="Verification evidence is missing.",
            route="report",
        )
    current_fingerprint = issue_set_fingerprint(state.verification_issues)
    previous = state.issue_fingerprints[-1] if state.issue_fingerprints else None
    repeated_diff = len(state.diff_fingerprints) != len(set(state.diff_fingerprints))
    no_progress = current_fingerprint == previous or repeated_diff
    state.issue_fingerprints.append(current_fingerprint)
    if (
        not state.verification.passed
        and not no_progress
        and state.repair_round < state.request.max_repair_rounds
        and (
            state.verification.remaining_issue_ids
            or state.verification.regression_issue_ids
        )
    ):
        return _event(
            state,
            stage="regression_check",
            message="Verification did not improve enough; returning to bounded repair planning.",
            route="retry",
        )
    if no_progress and not state.verification.passed:
        state.messages.append(
            WorkflowMessage(
                level="warning",
                stage="regression_check",
                text="The same issue/diff fingerprint repeated; the repair loop was stopped.",
            )
        )
    return _event(
        state,
        stage="regression_check",
        message="Verification is ready for human review.",
        route="review",
    )


def preview_node(node_input: dict[str, Any]) -> Event:
    state = _coerce_state(node_input)
    base_url = os.getenv("ACCESSPILOT_PUBLIC_BASE_URL", "http://127.0.0.1:8000")
    state.preview = preview_service.build(state, base_url)
    return _event(
        state,
        stage="preview",
        message="Before/after screenshots, issue counts, and preview links are ready.",
    )


def merge_pr_writer_node(ctx: Context, node_input: dict[str, Any]) -> Event:
    state = _state_from_context(ctx)
    output = SpecialistPullRequestDraft.model_validate(node_input)
    deterministic = git_service.build_pr_draft(state)
    proposed_title = redact_text(output.title, limit=120).strip()
    safe_title = (
        proposed_title
        if proposed_title == output.title.strip() and "\n" not in proposed_title
        else deterministic.title
    )
    state.pr_draft = PullRequestDraft(
        title=safe_title or deterministic.title,
        body=deterministic.body,
        branch_name=state.request.branch_name,
        base_branch=deterministic.base_branch,
        commit_message=deterministic.commit_message,
        draft=state.request.draft_pull_request,
        remote_url=deterministic.remote_url,
    )
    return _event(
        state,
        stage="pr_writer",
        message="PR Agent prepared commit and pull-request text for review; nothing was pushed.",
    )


async def human_approval_node(ctx: Context, node_input: dict[str, Any]):
    state = (
        _state_from_context(ctx)
        if ctx.state.get("workflow_state")
        else _coerce_state(node_input)
    )
    if not state.verification:
        yield _event(
            state,
            stage="human_approval",
            message="Approval cannot proceed without verification.",
            route="rejected",
        )
        return
    interrupt_id = f"pr_{state.run_id}_{state.verification.diff_sha256[:12]}"
    response = ctx.resume_inputs.get(interrupt_id)
    if response is None:
        await local_app_runner.supervisor.stop(state.run_id)
        state.status = RunStatus.WAITING_FOR_APPROVAL
        yield _state_event_only(
            state,
            stage="human_approval",
            message="Waiting for explicit approval bound to the current verified diff.",
        )
        yield RequestInput(
            interrupt_id=interrupt_id,
            message=(
                "Review original issues, fixes, remaining issues, tests, diff, and PR draft. "
                "Respond with decision approve, reject, or revise and the expected diff hash."
            ),
            response_schema={
                "type": "object",
                "properties": {
                    "decision": {
                        "type": "string",
                        "enum": ["approve", "reject", "revise"],
                    },
                    "expected_diff_sha256": {"type": "string"},
                    "reviewer_note": {"type": "string"},
                },
                "required": ["decision", "expected_diff_sha256"],
            },
        )
        return

    if isinstance(response, dict) and isinstance(response.get("result"), dict):
        response = response["result"]
    response = response if isinstance(response, dict) else {"decision": "reject"}
    try:
        decision = ApprovalDecision(str(response.get("decision", "reject")).lower())
    except ValueError:
        decision = ApprovalDecision.REJECT
    expected = str(response.get("expected_diff_sha256", ""))
    if expected != state.verification.diff_sha256:
        decision = ApprovalDecision.REJECT
        state.messages.append(
            WorkflowMessage(
                level="error",
                stage="human_approval",
                text="Approval was rejected because the expected diff hash did not match.",
            )
        )
    if decision == ApprovalDecision.APPROVE and not state.verification.passed:
        decision = ApprovalDecision.REJECT
        state.messages.append(
            WorkflowMessage(
                level="error",
                stage="human_approval",
                text="Approval was rejected because deterministic verification did not pass.",
            )
        )
    state.approval = ApprovalRecord(
        decision=decision,
        diff_sha256=state.verification.diff_sha256,
        approved_head=git_service.head_sha(state.workspace())
        if state.workspace()
        else None,
        verification_fingerprint=verification_fingerprint(state.verification),
        publication_fingerprint=git_service.publication_fingerprint(state),
        reviewer_note=response.get("reviewer_note"),
    )
    route = decision.value
    status = {
        ApprovalDecision.APPROVE: RunStatus.APPROVED,
        ApprovalDecision.REJECT: RunStatus.REJECTED,
        ApprovalDecision.REVISE: RunStatus.RUNNING,
    }[decision]
    yield _event(
        state,
        stage="human_approval",
        message=f"Human review decision recorded: {decision.value}.",
        route=route,
        status=status,
    )


def revision_guard_node(node_input: dict[str, Any]) -> Event:
    state = _coerce_state(node_input)
    if not state.approval or state.approval.decision != ApprovalDecision.REVISE:
        return _event(
            state,
            stage="revision_guard",
            message="Revision routing lacked a matching human decision.",
            route="report",
            status=RunStatus.STOPPED,
        )
    note = state.approval.reviewer_note
    if state.repair_round >= state.request.max_repair_rounds:
        return _event(
            state,
            stage="revision_guard",
            message="The bounded repair-round limit was reached; no further revision was run.",
            route="report",
            status=RunStatus.STOPPED,
        )
    state.approval = None
    state.revision_feedback = note
    state.pr_draft = None
    state.preview = PreviewInfo()
    state.pull_request_url = None
    state.patch_proposals = []
    state.messages.append(
        WorkflowMessage(
            stage="revision_guard",
            text=f"Reviewer requested a bounded revision: {note or 'no note supplied'}",
        )
    )
    return _event(
        state,
        stage="revision_guard",
        message="Approval-derived state was invalidated before replanning.",
        route="repair",
        status=RunStatus.RUNNING,
    )


def pull_request_node(node_input: dict[str, Any]) -> Event:
    state = _coerce_state(node_input)
    if not state.approval or state.approval.decision != ApprovalDecision.APPROVE:
        raise RuntimeError("Pull-request node reached without approval")
    if state.request.execute_pull_request and get_settings().enable_remote_mutations:
        state.pull_request_url = git_service.execute_pull_request(state, state.approval)
        message = f"Pull request created: {state.pull_request_url}"
    else:
        message = (
            "Approval recorded; push/PR execution remained disabled for this dry run."
        )
    return _event(state, stage="pull_request", message=message)


async def final_report_node(node_input: dict[str, Any]):
    state = _coerce_state(node_input)
    await local_app_runner.supervisor.stop(state.run_id)
    if state.status not in {RunStatus.REJECTED, RunStatus.FAILED, RunStatus.STOPPED}:
        state.status = RunStatus.COMPLETED
    state.stage = "final_report"
    state.updated_at = utc_now()
    limitations = [
        "Automated scans do not replace screen-reader and usability testing.",
        "Subjective alternative text and complex keyboard interactions require human review.",
    ]
    if not state.target or not state.target.source_available:
        limitations.append(
            "No source was available, so AccessPilot did not apply repairs."
        )
    report = FinalReport(
        run_id=state.run_id,
        status=state.status,
        target=state.request.target,
        framework=state.framework,
        original_issues=state.issues,
        applied_changes=state.changes,
        remaining_issue_ids=(
            state.verification.remaining_issue_ids
            if state.verification
            else state.unresolved_issue_ids
        ),
        verification=state.verification,
        preview=state.preview,
        pr_draft=state.pr_draft,
        approval=state.approval,
        limitations=limitations,
    )
    data = state.model_dump(mode="json")
    actions = EventActions()
    actions.state_delta["workflow_state"] = data
    yield Event(actions=actions)
    summary = (
        f"AccessPilot completed run {state.run_id}: "
        f"{len(state.issues)} original issue(s), {len(state.changes)} changed file(s)."
    )
    yield Event(
        content=types.Content(role="model", parts=[types.Part.from_text(text=summary)]),
        output=report.model_dump(mode="json"),
    )


def _fallback_auditor(ctx: Context, node_input: dict[str, Any]) -> dict[str, Any]:
    state = _state_from_context(ctx)
    return AuditorOutput(
        issues=[
            AuditedIssue(
                issue_id=issue.id,
                title=issue.title,
                severity=issue.severity.value,
                wcag_references=issue.wcag_references,
                affected_user_experience=issue.explanation,
                explanation=issue.explanation,
                suggested_fix_strategy=issue.suggested_fix,
                confidence=issue.confidence,
                evidence_sufficient=True,
            )
            for issue in state.issues
        ],
        caveats=[
            "Deterministic specialist fallback was used; no LLM interpretation was added."
        ],
    ).model_dump(mode="json")


def _fallback_investigator(ctx: Context, node_input: dict[str, Any]) -> dict[str, Any]:
    state = _state_from_context(ctx)
    return InvestigatorOutput(
        mappings=[
            IssueSourceMapping(
                issue_id=mapping.issue_id,
                candidates=[
                    SourceCandidate(
                        path=candidate.path,
                        symbol_or_region=candidate.snippet or "source match",
                        evidence=candidate.snippet or "deterministic source match",
                        confidence=candidate.confidence,
                    )
                    for candidate in mapping.candidates
                ],
                change_types=["html"],
                unresolved_reason=""
                if mapping.candidates
                else "No deterministic match.",
            )
            for mapping in state.mappings
        ],
        caveats=[],
    ).model_dump(mode="json")


def _fallback_planner(ctx: Context, node_input: dict[str, Any]) -> dict[str, Any]:
    state = _state_from_context(ctx)
    if not state.workspace():
        return PlannerOutput(
            change_groups=[],
            execution_order=[],
            unresolved_issue_ids=[issue.id for issue in state.issues],
            stop_reason="No source workspace is available.",
        ).model_dump(mode="json")
    plan = repair_engine.plan(
        state.workspace(),
        state.verification_issues or state.issues,
        state.mappings,
    )
    groups = [
        PlannedChangeGroup(
            group_id=f"group-{action.id}",
            issue_ids=action.issue_ids,
            files=[action.file_path],
            strategy=action.strategy,
            test_updates=[],
            risk=action.risk.value,
            dependencies=[],
        )
        for action in plan.actions
    ]
    return PlannerOutput(
        change_groups=groups,
        execution_order=[group.group_id for group in groups],
        unresolved_issue_ids=plan.unresolved_issue_ids,
        stop_reason="",
    ).model_dump(mode="json")


def _fallback_repairer(ctx: Context, node_input: dict[str, Any]) -> dict[str, Any]:
    _state_from_context(ctx)
    return RepairerOutput(
        edits=[],
        skipped_issue_ids=[],
        warnings=[
            "Deterministic repair fallback is active; policy-owned patch functions will apply supported changes."
        ],
    ).model_dump(mode="json")


def _fallback_verifier(ctx: Context, node_input: dict[str, Any]) -> dict[str, Any]:
    state = _state_from_context(ctx)
    report = state.verification
    return VerifierOutput(
        resolved_issue_ids=report.fixed_issue_ids if report else [],
        remaining_issue_ids=report.remaining_issue_ids if report else [],
        regression_issue_ids=report.regression_issue_ids if report else [],
        checks=[
            CheckAssessment(
                name=" ".join(check.argv),
                status=(
                    "skipped"
                    if check.skipped_reason
                    else ("passed" if check.exit_code == 0 else "failed")
                ),
                evidence=check.skipped_reason or check.stderr or check.stdout,
            )
            for check in (report.checks if report else [])
        ],
        verdict="pass" if report and report.passed else "stop",
        summary=report.summary if report else "Verification is unavailable.",
    ).model_dump(mode="json")


def _fallback_pr_writer(ctx: Context, node_input: dict[str, Any]) -> dict[str, Any]:
    state = _state_from_context(ctx)
    draft = git_service.build_pr_draft(state)
    return SpecialistPullRequestDraft(
        branch_name=draft.branch_name,
        commit_message=draft.commit_message,
        title=draft.title,
        body=draft.body,
        wcag_references=sorted(
            {reference for issue in state.issues for reference in issue.wcag_references}
        ),
        tests_run=[
            " ".join(check.argv)
            for check in (state.verification.checks if state.verification else [])
        ],
        remaining_issues=state.verification.remaining_issue_ids
        if state.verification
        else [],
        ready_for_human_review=bool(state.verification),
        warnings=[],
    ).model_dump(mode="json")


def _function_node(
    func,
    name: str,
    *,
    retry: bool = False,
    rerun_on_resume: bool = False,
    wait_for_output: bool = False,
) -> FunctionNode:
    node = FunctionNode(
        func=func,
        name=name,
        retry_config=(
            RetryConfig(max_attempts=2, initial_delay=0.25, max_delay=2.0, jitter=0.1)
            if retry
            else None
        ),
        timeout=600
        if name
        in {"LocalAppRunnerNode", "AccessibilityScanNode", "VerificationScanNode"}
        else 180,
        rerun_on_resume=rerun_on_resume,
    )
    node.wait_for_output = wait_for_output
    return node


def build_accesspilot_workflow(
    *,
    llm_config: LLMConfig | None = None,
    deterministic_specialists: bool = False,
) -> Workflow:
    """Build a fresh executable workflow so graph nodes never share parents."""
    if deterministic_specialists:
        specialists = {
            "auditor": _function_node(
                _fallback_auditor, "AccessibilityAuditorAgentNode"
            ),
            "investigator": _function_node(
                _fallback_investigator, "CodebaseInvestigatorAgentNode"
            ),
            "planner": _function_node(_fallback_planner, "RepairPlanningAgentNode"),
            "repairer": _function_node(_fallback_repairer, "CodeRepairAgentNode"),
            "verifier": _function_node(_fallback_verifier, "VerificationAgentNode"),
            "pr_writer": _function_node(_fallback_pr_writer, "PRAgentNode"),
        }
    else:
        specialists = create_specialist_agents(create_llm_model(llm_config))

    intake = _function_node(intake_node, "IntakeNode")
    intake.input_schema = AccessPilotRequest
    loader = _function_node(repository_loader_node, "RepositoryLoaderNode", retry=True)
    detector = _function_node(framework_detector_node, "FrameworkDetectorNode")
    runner = _function_node(local_app_runner_node, "LocalAppRunnerNode", retry=True)
    routes = _function_node(route_discovery_node, "RouteDiscoveryNode")
    scan = _function_node(accessibility_scan_node, "AccessibilityScanNode", retry=True)
    normalize = _function_node(issue_normalization_node, "IssueNormalizationNode")
    audit_input = _function_node(auditor_input_node, "AuditorInputNode")
    merge_audit = _function_node(merge_auditor_node, "AuditorMergeNode")
    source_map = _function_node(source_mapping_node, "SourceMappingNode")
    investigator_input = _function_node(
        investigator_input_node, "InvestigatorInputNode"
    )
    merge_investigator = _function_node(
        merge_investigator_node, "InvestigatorMergeNode"
    )
    repairable = _function_node(repairability_router_node, "RepairabilityGuardNode")
    planner_input = _function_node(planner_input_node, "PlannerInputNode")
    merge_planner = _function_node(merge_planner_node, "RepairPlanPolicyNode")
    repairer_input = _function_node(repairer_input_node, "RepairerInputNode")
    merge_repairer = _function_node(merge_repairer_node, "RepairProposalGuardNode")
    patch = _function_node(patch_validation_node, "PatchValidationNode")
    verify_scan = _function_node(
        verification_scan_node, "VerificationScanNode", retry=True
    )
    verifier_input = _function_node(verifier_input_node, "VerifierInputNode")
    merge_verifier = _function_node(merge_verifier_node, "VerificationMergeNode")
    regression = _function_node(regression_check_node, "RegressionCheckNode")
    preview = _function_node(preview_node, "PreviewNode")
    pr_input = _function_node(pr_writer_input_node, "PRWriterInputNode")
    merge_pr = _function_node(merge_pr_writer_node, "PRDraftGuardNode")
    approval = _function_node(
        human_approval_node,
        "HumanApprovalNode",
        rerun_on_resume=True,
        wait_for_output=True,
    )
    pull_request = _function_node(pull_request_node, "PullRequestNode")
    revision_guard = _function_node(revision_guard_node, "RevisionGuardNode")
    final = _function_node(final_report_node, "FinalReportNode")

    return Workflow(
        name="accesspilot_workflow",
        description="Detect, repair, verify, review, and publish accessibility fixes.",
        input_schema=AccessPilotRequest,
        output_schema=FinalReport,
        timeout=1800,
        max_concurrency=4,
        edges=[
            ("START", intake, loader, detector, runner),
            (runner, {"scan": routes, "report": final}),
            (
                routes,
                scan,
                normalize,
                audit_input,
                specialists["auditor"],
                merge_audit,
                source_map,
                investigator_input,
                specialists["investigator"],
                merge_investigator,
                repairable,
            ),
            (repairable, {"repair": planner_input, "report": final}),
            (
                planner_input,
                specialists["planner"],
                merge_planner,
                repairer_input,
                specialists["repairer"],
                merge_repairer,
                patch,
            ),
            (patch, {"verify": verify_scan, "report": final}),
            (
                verify_scan,
                verifier_input,
                specialists["verifier"],
                merge_verifier,
                regression,
            ),
            (
                regression,
                {"retry": source_map, "review": preview, "report": final},
            ),
            (preview, pr_input, specialists["pr_writer"], merge_pr, approval),
            (
                approval,
                {
                    "approve": pull_request,
                    "revise": revision_guard,
                    "reject": final,
                },
            ),
            (revision_guard, {"repair": planner_input, "report": final}),
            (pull_request, final),
        ],
    )


__all__ = ["build_accesspilot_workflow"]
