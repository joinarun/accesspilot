"""In-memory execution and resumable approval management for the local UI."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid
from dataclasses import dataclass, field

from google.adk.apps import App, ResumabilityConfig
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.providers import LLMConfig, LLMProvider
from app.schemas import (
    AccessPilotRequest,
    ApprovalRequest,
    LlmProviderName,
    RunAccepted,
    RunEvent,
    RunSnapshot,
    RunStatus,
    WorkflowMessage,
    WorkflowState,
    utc_now,
)
from app.services.safety import redact_text, validate_editable_path
from app.workflow import build_accesspilot_workflow

logger = logging.getLogger("accesspilot.ai")


@dataclass
class RunRecord:
    run_id: str
    request: AccessPilotRequest
    state: WorkflowState
    runner: Runner
    events: list[RunEvent] = field(default_factory=list)
    interrupt_id: str | None = None
    task: asyncio.Task[None] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class RunManager:
    def __init__(self) -> None:
        self._session_service = InMemorySessionService()
        self._runs: dict[str, RunRecord] = {}
        self._lock = asyncio.Lock()

    async def create(self, request: AccessPilotRequest) -> RunAccepted:
        run_id = uuid.uuid4().hex
        explicitly_deterministic = request.llm_provider is LlmProviderName.DETERMINISTIC
        forced_deterministic = _truthy(
            os.getenv("ACCESSPILOT_DETERMINISTIC_SPECIALISTS")
        )
        provider_configured = (
            False if explicitly_deterministic else _provider_configured(request)
        )
        deterministic = (
            explicitly_deterministic or forced_deterministic or not provider_configured
        )
        llm_config = LLMConfig.from_env()
        if explicitly_deterministic:
            provider_name = LlmProviderName.DETERMINISTIC.value
            model_name = "none"
            specialist_mode = "deterministic"
        else:
            llm_config = llm_config.model_copy(
                update={"provider": LLMProvider(request.llm_provider.value)}
            )
            provider_name = llm_config.provider.value
            model_name = (
                llm_config.openai.model
                if llm_config.provider is LLMProvider.OPENAI
                else llm_config.gemini.model
            ).removeprefix("openai/")
            if forced_deterministic:
                specialist_mode = "deterministic_forced"
            elif not provider_configured:
                specialist_mode = "deterministic_fallback"
            else:
                specialist_mode = "llm"
        logger.info(
            "event=ai_run_routing run_id=%s provider=%s model=%s mode=%s",
            run_id,
            provider_name,
            model_name,
            specialist_mode,
        )
        workflow = build_accesspilot_workflow(
            llm_config=llm_config,
            deterministic_specialists=deterministic,
        )
        adk_app = App(
            name="app",
            root_agent=workflow,
            resumability_config=ResumabilityConfig(is_resumable=True),
        )
        await self._session_service.create_session(
            app_name="app", user_id="local-reviewer", session_id=run_id
        )
        runner = Runner(app=adk_app, session_service=self._session_service)
        initial = WorkflowState(
            run_id=run_id,
            request=request,
            status=RunStatus.QUEUED,
        )
        initial.messages.append(
            WorkflowMessage(
                level="info",
                stage="intake",
                text=(
                    "AI routing: "
                    f"provider={provider_name}, "
                    f"model={model_name}, mode={specialist_mode}."
                ),
            )
        )
        if deterministic:
            initial.messages.append(
                WorkflowMessage(
                    level="warning",
                    stage="intake",
                    text=(
                        "Deterministic specialists were selected for this run."
                        if explicitly_deterministic
                        else (
                            "Deterministic specialists were explicitly forced by server "
                            "configuration."
                            if forced_deterministic
                            else (
                                f"{request.llm_provider.value} credentials are not "
                                "configured; the typed deterministic specialist fallback "
                                "will be used."
                            )
                        )
                    ),
                )
            )
        record = RunRecord(run_id=run_id, request=request, state=initial, runner=runner)
        async with self._lock:
            self._runs[run_id] = record
        record.task = asyncio.create_task(self._start(record))
        return RunAccepted(
            run_id=run_id,
            status=RunStatus.QUEUED,
            poll_url=f"/api/runs/{run_id}",
        )

    def snapshot(self, run_id: str) -> RunSnapshot:
        record = self._require(run_id)
        return RunSnapshot(
            state=record.state.model_copy(deep=True),
            events=[event.model_copy(deep=True) for event in record.events],
        )

    def state(self, run_id: str) -> WorkflowState:
        return self._require(run_id).state

    async def approve(self, run_id: str, approval: ApprovalRequest) -> RunSnapshot:
        record = self._require(run_id)
        async with record.lock:
            if (
                record.state.status != RunStatus.WAITING_FOR_APPROVAL
                or not record.interrupt_id
            ):
                raise ValueError("Run is not waiting for approval")
            if (
                not record.state.verification
                or approval.expected_diff_sha256
                != record.state.verification.diff_sha256
            ):
                raise ValueError("Approval diff hash does not match the current review")
            workspace = record.state.workspace()
            if not workspace or not record.state.changes:
                raise ValueError("Approval requires a reviewable source patch")
            for change in record.state.changes:
                try:
                    path = validate_editable_path(workspace, change.path)
                    current_hash = hashlib.sha256(path.read_bytes()).hexdigest()
                except (OSError, RuntimeError) as exc:
                    raise ValueError(
                        "The reviewed working copy is no longer available"
                    ) from exc
                if current_hash != change.after_sha256:
                    raise ValueError(
                        "A reviewed source file changed after verification"
                    )
            response = types.Content(
                role="user",
                parts=[
                    types.Part.from_function_response(
                        name="adk_request_input",
                        response=approval.model_dump(mode="json"),
                    )
                ],
            )
            # google-genai's convenience constructor does not expose the function
            # call id, but ADK resumability requires it to bind the exact interrupt.
            response.parts[0].function_response.id = record.interrupt_id
            record.state.status = RunStatus.RUNNING
            record.task = asyncio.create_task(self._drive(record, response))
        return self.snapshot(run_id)

    async def _start(self, record: RunRecord) -> None:
        message = types.Content(
            role="user",
            parts=[
                types.Part.from_text(
                    text=json.dumps(record.request.model_dump(mode="json"))
                )
            ],
        )
        await self._drive(record, message)

    async def _drive(self, record: RunRecord, message: types.Content) -> None:
        try:
            async for event in record.runner.run_async(
                user_id="local-reviewer",
                session_id=record.run_id,
                new_message=message,
            ):
                self._consume_event(record, event)
        except Exception as exc:
            record.state.status = RunStatus.FAILED
            record.state.stage = "failed"
            record.state.error = redact_text(str(exc), limit=2000)
            record.state.updated_at = utc_now()
            record.events.append(
                RunEvent(
                    sequence=len(record.events) + 1,
                    run_id=record.run_id,
                    stage="failed",
                    status=RunStatus.FAILED,
                    message=record.state.error or type(exc).__name__,
                )
            )

    def _consume_event(self, record: RunRecord, event) -> None:
        state_delta = getattr(event.actions, "state_delta", {}) or {}
        if isinstance(state_delta.get("workflow_state"), dict):
            record.state = WorkflowState.model_validate(state_delta["workflow_state"])
        if (
            isinstance(event.output, dict)
            and "run_id" in event.output
            and "request" in event.output
        ):
            record.state = WorkflowState.model_validate(event.output)
        if event.content and event.content.parts:
            for part in event.content.parts:
                function_call = part.function_call
                if function_call and function_call.name == "adk_request_input":
                    record.interrupt_id = function_call.id
                    record.state.status = RunStatus.WAITING_FOR_APPROVAL
        message = _event_message(event, record.state)
        if message:
            record.events.append(
                RunEvent(
                    sequence=len(record.events) + 1,
                    run_id=record.run_id,
                    stage=record.state.stage,
                    status=record.state.status,
                    message=message,
                )
            )

    def _require(self, run_id: str) -> RunRecord:
        try:
            return self._runs[run_id]
        except KeyError as exc:
            raise KeyError(f"Unknown run: {run_id}") from exc


def _event_message(event, state: WorkflowState) -> str:
    if isinstance(event.output, dict) and "run_id" in event.output:
        if state.messages:
            return state.messages[-1].text
        return state.stage
    if event.content and event.content.parts:
        texts = [part.text for part in event.content.parts if part.text]
        if texts:
            return redact_text(" ".join(texts), limit=1000)
        if any(part.function_call for part in event.content.parts):
            return "Human approval is required before any push or pull request."
    return ""


def _provider_configured(request: AccessPilotRequest) -> bool:
    if request.llm_provider.value == "openai":
        base = os.getenv("LLMPROXY_BASE_URL", "").strip()
        key = os.getenv("LLMPROXY_API_KEY", "").strip()
        return _real_configuration_value(base) and _real_configuration_value(key)
    key = (
        os.getenv("GOOGLE_API_KEY", "").strip()
        or os.getenv("GEMINI_API_KEY", "").strip()
    )
    if _real_configuration_value(key):
        return True
    vertex = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
    return vertex and _real_configuration_value(project)


def _truthy(value: str | None) -> bool:
    return bool(value and value.strip().lower() in {"1", "true", "yes", "on"})


def _real_configuration_value(value: str) -> bool:
    lowered = value.strip().lower()
    return bool(value.strip()) and not lowered.startswith(
        (
            "replace-",
            "replace_",
            "provided-",
            "provided_",
            "your-",
            "your_",
            "my-",
            "my_",
        )
    )


run_manager = RunManager()
