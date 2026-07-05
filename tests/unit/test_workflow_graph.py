from pathlib import Path

from google.adk.workflow import Workflow

from app.schemas import AccessPilotRequest, WorkflowState
from app.workflow import (
    _allow_private_scan,
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
