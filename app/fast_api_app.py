"""Combined ADK API, AccessPilot REST API, previews, and built web UI."""

from __future__ import annotations

import contextlib
import ipaddress
import logging
import os
from collections.abc import AsyncIterator

from a2a.server.tasks import InMemoryTaskStore
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from google.adk.agents import BaseAgent
from google.adk.cli.fast_api import get_fast_api_app
from google.adk.runners import Runner

from app.app_utils import services
from app.app_utils.a2a import attach_a2a_routes
from app.app_utils.telemetry import setup_telemetry
from app.app_utils.typing import Feedback
from app.providers import LLMConfig
from app.run_manager import run_manager
from app.schemas import (
    AccessPilotRequest,
    ApprovalRequest,
    LlmProviderName,
    ProviderAvailability,
    PublicConfig,
    RunAccepted,
    RunSnapshot,
)
from app.services.previews import preview_service
from app.services.process import process_supervisor
from app.settings import PROJECT_ROOT, get_settings

load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("accesspilot")
setup_telemetry()
settings = get_settings()

allow_origins = (
    os.getenv("ALLOW_ORIGINS", "").split(",")
    if os.getenv("ALLOW_ORIGINS")
    else ["http://127.0.0.1:5173", "http://localhost:5173"]
)
AGENT_DIR = str(PROJECT_ROOT)


def _configured_env(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    lowered = value.lower()
    if not value or lowered.startswith(
        ("replace-", "replace_", "provided-", "provided_", "your-", "your_")
    ):
        return None
    return value


def _loopback_client(scope: dict) -> bool:
    client = scope.get("client")
    host = client[0] if client else ""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


class LocalClientPolicyMiddleware:
    """Apply the local-only default to HTTP and native ADK WebSockets."""

    def __init__(self, app, *, allow_remote: bool = False) -> None:
        self.app = app
        self.allow_remote = allow_remote

    async def __call__(self, scope, receive, send) -> None:
        protected = scope["type"] in {"http", "websocket"}
        health = scope["type"] == "http" and scope.get("path") in {
            "/health",
            "/api/health",
        }
        if (
            protected
            and not health
            and not self.allow_remote
            and not _loopback_client(scope)
        ):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            response = JSONResponse(
                status_code=403,
                content={
                    "detail": "AccessPilot accepts loopback clients only by default."
                },
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


@contextlib.asynccontextmanager
async def lifespan(api: FastAPI) -> AsyncIterator[None]:
    from app.agent import app as adk_app
    from app.agent import root_agent

    runner = Runner(
        app=adk_app,
        session_service=services.get_session_service(),
        artifact_service=services.get_artifact_service(),
        auto_create_session=True,
    )
    api.state.runner = runner
    api.state.agent_app_name = adk_app.name
    if isinstance(root_agent, BaseAgent):
        await attach_a2a_routes(
            api,
            agent=root_agent,
            runner=runner,
            task_store=InMemoryTaskStore(),
            rpc_path=f"/a2a/{adk_app.name}",
        )
    else:
        logger.info("A2A routes are skipped because ADK Workflow is a BaseNode")
    try:
        yield
    finally:
        await process_supervisor.stop_all()


app: FastAPI = get_fast_api_app(
    agents_dir=AGENT_DIR,
    web=False,
    artifact_service_uri=services.ARTIFACT_SERVICE_URI,
    allow_origins=allow_origins,
    session_service_uri=services.SESSION_SERVICE_URI,
    otel_to_cloud=False,
    lifespan=lifespan,
)
app.title = "AccessPilot"
app.description = "Autonomous accessibility scan, repair, verification, and review API"
app.add_middleware(
    LocalClientPolicyMiddleware,
    allow_remote=settings.allow_remote_clients,
)


@app.get("/health")
@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "healthy", "service": "accesspilot"}


@app.get("/api/config", response_model=PublicConfig)
async def public_config() -> PublicConfig:
    config = LLMConfig.from_env()
    gemini_configured = bool(
        _configured_env("GOOGLE_API_KEY")
        or _configured_env("GEMINI_API_KEY")
        or (
            os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in {"1", "true", "yes"}
            and _configured_env("GOOGLE_CLOUD_PROJECT")
        )
    )
    proxy_configured = bool(
        _configured_env("LLMPROXY_BASE_URL") and _configured_env("LLMPROXY_API_KEY")
    )
    return PublicConfig(
        providers=[
            ProviderAvailability(
                name=LlmProviderName.GEMINI,
                configured=gemini_configured,
                model=config.gemini.model,
                detail=(
                    "Native Gemini credentials are configured."
                    if gemini_configured
                    else "Configure Gemini credentials; deterministic fallback remains available."
                ),
            ),
            ProviderAvailability(
                name=LlmProviderName.OPENAI,
                configured=proxy_configured,
                model=config.openai.model,
                detail=(
                    "Enterprise LLMproxy is configured."
                    if proxy_configured
                    else "Configure LLMPROXY_BASE_URL and LLMPROXY_API_KEY."
                ),
            ),
        ],
        default_provider=LlmProviderName(config.provider.value),
        max_repair_rounds=settings.max_repair_rounds,
        sample_target="sample://vulnerable-app",
        local_execution_enabled=settings.enable_local_execution,
        dependency_install_enabled=settings.enable_dependency_install,
        remote_mutations_enabled=settings.enable_remote_mutations,
    )


@app.post("/api/runs", response_model=RunAccepted, status_code=status.HTTP_202_ACCEPTED)
async def create_run(request: AccessPilotRequest) -> RunAccepted:
    if request.allow_local_execution and not settings.enable_local_execution:
        raise HTTPException(
            status_code=403,
            detail="Local application execution is disabled by server policy.",
        )
    if request.allow_dependency_install and not settings.enable_dependency_install:
        raise HTTPException(
            status_code=403,
            detail="Dependency installation is disabled by server policy.",
        )
    if request.execute_pull_request and not settings.enable_remote_mutations:
        raise HTTPException(
            status_code=403,
            detail="Remote Git and pull-request mutation is disabled by server policy.",
        )
    return await run_manager.create(request)


@app.get("/api/runs/{run_id}", response_model=RunSnapshot)
async def get_run(run_id: str) -> RunSnapshot:
    try:
        return run_manager.snapshot(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}/issues")
async def get_issues(run_id: str):
    try:
        state = run_manager.state(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"original": state.issues, "verification": state.verification_issues}


@app.get("/api/runs/{run_id}/plan")
async def get_plan(run_id: str):
    try:
        return run_manager.state(run_id).repair_plan
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}/verification")
async def get_verification(run_id: str):
    try:
        return run_manager.state(run_id).verification
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}/pr-draft")
async def get_pr_draft(run_id: str):
    try:
        return run_manager.state(run_id).pr_draft
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post(
    "/api/runs/{run_id}/approval",
    response_model=RunSnapshot,
    status_code=status.HTTP_202_ACCEPTED,
)
async def approve_run(run_id: str, approval: ApprovalRequest) -> RunSnapshot:
    try:
        return await run_manager.approve(run_id, approval)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/previews/{run_id}/fixed/{asset_path:path}")
async def fixed_preview(run_id: str, asset_path: str) -> FileResponse:
    try:
        state = run_manager.state(run_id)
        path = preview_service.resolve_fixed_file(state, asset_path)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Preview file not found") from exc
    return FileResponse(
        path,
        headers={
            "Content-Security-Policy": (
                "sandbox allow-same-origin; default-src 'none'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; font-src 'self'; script-src 'none'; "
                "connect-src 'none'; form-action 'none'; base-uri 'none'"
            ),
            "Cross-Origin-Resource-Policy": "same-origin",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/api/artifacts/{run_id}/{artifact_path:path}")
async def artifact(run_id: str, artifact_path: str) -> FileResponse:
    try:
        path = preview_service.resolve_artifact(run_id, artifact_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Artifact not found") from exc
    return FileResponse(path)


@app.post("/feedback")
def collect_feedback(feedback: Feedback) -> dict[str, str]:
    logger.info("feedback score=%s service=%s", feedback.score, feedback.service_name)
    return {"status": "success"}


app.mount(
    "/demo/original",
    StaticFiles(directory=str(settings.sample_root), html=True),
    name="vulnerable-demo",
)


if settings.frontend_dist.exists():
    app.mount(
        "/",
        StaticFiles(directory=str(settings.frontend_dist), html=True),
        name="accesspilot-ui",
    )
else:

    @app.get("/", response_class=HTMLResponse)
    async def development_index() -> str:
        return """
        <!doctype html><html lang="en"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <title>AccessPilot</title></head><body><main>
        <h1>AccessPilot API is running</h1>
        <p>Build the React interface with <code>npm install && npm run build</code>
        in <code>frontend/</code>, or use its Vite development server.</p>
        <p><a href="/docs">Open API documentation</a></p>
        </main></body></html>
        """


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
