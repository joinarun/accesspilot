"""ADK entry point for the AccessPilot graph workflow."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from google.adk.apps import App, ResumabilityConfig

from app.providers import ProviderConfigurationError
from app.workflow import build_accesspilot_workflow

load_dotenv()


def _truthy(value: str | None) -> bool:
    return bool(value and value.strip().lower() in {"1", "true", "yes", "on"})


try:
    root_agent = build_accesspilot_workflow(
        deterministic_specialists=_truthy(
            os.getenv("ACCESSPILOT_DETERMINISTIC_SPECIALISTS")
        )
    )
except ProviderConfigurationError:
    # Keep local tools, the UI, graph inspection, and deterministic tests usable
    # before credentials are configured. Setting valid provider credentials and
    # reloading the process automatically restores the LLM specialist nodes.
    root_agent = build_accesspilot_workflow(deterministic_specialists=True)


app = App(
    name="app",
    root_agent=root_agent,
    resumability_config=ResumabilityConfig(is_resumable=True),
)


__all__ = ["app", "root_agent"]
