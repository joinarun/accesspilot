from pathlib import Path

from google.adk.workflow import Workflow

from app.schemas import (
    AccessPilotRequest,
    RepairAction,
    RepairPlan,
    RiskLevel,
    WorkflowState,
)
from app.specialists.schemas import PlannedChangeGroup, PlannerOutput
from app.workflow import (
    _allow_private_scan,
    _apply_planner_stop_policy,
    _is_managed_loopback_app,
    _is_trusted_crawl_target,
    build_accesspilot_workflow,
)


def test_graph_contains_required_nodes_and_bounded_back_edges() -> None:
    workflow = build_accesspilot_workflow(deterministic_specialists=True)
    assert isinstance(workflow, Workflow)
    names = {node.name for node in workflow.graph.nodes}
    required = {
        "IntakeNode",
        "RepositoryLoaderNode",
        "FrameworkDetectorNode",
        "LocalAppRunnerNode",
        "RouteDiscoveryNode",
        "AccessibilityScanNode",
        "IssueNormalizationNode",
        "PlannerInputNode",
        "RepairPlanningAgentNode",
        "SourceMappingNode",
        "CodeRepairAgentNode",
        "PatchValidationNode",
        "PreviewNode",
        "VerificationScanNode",
        "RegressionCheckNode",
        "HumanApprovalNode",
        "RevisionGuardNode",
        "PullRequestNode",
        "FinalReportNode",
    }
    assert required <= names

    routed_edges = [edge for edge in workflow.graph.edges if edge.route is not None]
    assert routed_edges
    assert any(
        edge.from_node.name == "RegressionCheckNode"
        and edge.to_node.name == "SourceMappingNode"
        and edge.route == "retry"
        for edge in routed_edges
    )


def test_local_execution_does_not_trust_a_caller_supplied_url(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ACCESSPILOT_ALLOW_PRIVATE_NETWORK_TARGETS", "false")
    state = WorkflowState(
        request=AccessPilotRequest(
            target=str(tmp_path),
            app_url="https://public.example.test/",
            allow_local_execution=True,
        ),
        workspace_path=str(tmp_path),
        app_url="https://public.example.test/",
    )

    assert _is_managed_loopback_app(state) is False
    assert _is_trusted_crawl_target(state) is False
    assert _allow_private_scan(state) is False


def test_managed_loopback_app_receives_only_local_scan_trust(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ACCESSPILOT_ALLOW_PRIVATE_NETWORK_TARGETS", "false")
    state = WorkflowState(
        request=AccessPilotRequest(
            target=str(tmp_path),
            allow_local_execution=True,
        ),
        workspace_path=str(tmp_path),
        app_url="http://127.0.0.1:43123/",
    )

    assert _is_managed_loopback_app(state) is True
    assert _is_trusted_crawl_target(state) is True
    assert _allow_private_scan(state) is True


def test_planner_caution_keeps_only_policy_backed_actions() -> None:
    plan = RepairPlan(
        summary="Apply two repairs.",
        actions=[
            RepairAction(
                id="automatic",
                issue_ids=["safe"],
                file_path="index.html",
                strategy="Add a language attribute.",
                rationale="Deterministic repair.",
                change_kind="html",
                risk=RiskLevel.LOW,
                confidence=0.95,
                expected_result="Resolve the finding.",
                automatic=True,
            ),
            RepairAction(
                id="proposal",
                issue_ids=["proposal"],
                file_path="component.tsx",
                strategy="Update the component.",
                rationale="Agent-proposed repair.",
                change_kind="tsx",
                risk=RiskLevel.MEDIUM,
                confidence=0.5,
                expected_result="Resolve the finding.",
                automatic=False,
            ),
        ],
        overall_risk=RiskLevel.MEDIUM,
    )
    output = PlannerOutput(
        change_groups=[
            PlannedChangeGroup(
                group_id="safe-group",
                issue_ids=["safe"],
                files=["index.html"],
                strategy="Apply the safe repair.",
                test_updates=[],
                risk="low",
                dependencies=[],
            )
        ],
        execution_order=["safe-group"],
        unresolved_issue_ids=["unsupported"],
        stop_reason="Some findings require human review.",
    )

    result = _apply_planner_stop_policy(
        plan,
        output,
        known_issue_ids={"safe", "proposal", "unsupported"},
    )

    assert [action.id for action in result.actions] == ["automatic"]
    assert result.unresolved_issue_ids == ["proposal", "unsupported"]


def test_planner_complete_stop_discards_all_actions() -> None:
    plan = RepairPlan(
        summary="Apply one repair.",
        actions=[
            RepairAction(
                id="automatic",
                issue_ids=["safe"],
                file_path="index.html",
                strategy="Add a language attribute.",
                rationale="Deterministic repair.",
                change_kind="html",
                risk=RiskLevel.LOW,
                confidence=0.95,
                expected_result="Resolve the finding.",
                automatic=True,
            )
        ],
        overall_risk=RiskLevel.LOW,
    )
    output = PlannerOutput(
        change_groups=[],
        execution_order=[],
        unresolved_issue_ids=["safe"],
        stop_reason="No safe source ownership is available.",
    )

    result = _apply_planner_stop_policy(
        plan,
        output,
        known_issue_ids={"safe"},
    )

    assert result.actions == []
    assert result.unresolved_issue_ids == ["safe"]
