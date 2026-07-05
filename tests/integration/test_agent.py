from app.agent import app, root_agent


def test_adk_app_exposes_resumable_accesspilot_workflow() -> None:
    assert app.name == "app"
    assert app.resumability_config.is_resumable is True
    assert root_agent.name == "accesspilot_workflow"
    assert root_agent.graph is not None
