"""Local app startup and URL fallback decisions."""

from __future__ import annotations

import os
import socket
from pathlib import Path

from app.schemas import (
    AccessPilotRequest,
    FrameworkInfo,
    FrameworkKind,
    ResolvedTarget,
    TargetKind,
)

from .errors import AccessPilotError
from .process import ProcessSupervisor, process_supervisor
from .safety import validate_external_http_url, validate_http_url


class LocalAppRunner:
    def __init__(self, supervisor: ProcessSupervisor | None = None) -> None:
        self.supervisor = supervisor or process_supervisor

    async def resolve_or_start(
        self,
        *,
        run_id: str,
        request: AccessPilotRequest,
        target: ResolvedTarget,
        framework: FrameworkInfo | None,
        workspace: Path | None,
    ) -> str | None:
        if target.kind == TargetKind.WEBSITE:
            return target.normalized_url
        if request.demo_mode or request.target == "sample://vulnerable-app":
            base = os.getenv("ACCESSPILOT_PUBLIC_BASE_URL", "http://127.0.0.1:8000")
            return validate_http_url(f"{base.rstrip('/')}/demo/original/")
        if request.app_url:
            return validate_external_http_url(
                request.app_url,
                allow_private=self.supervisor.settings.allow_private_network_targets,
            )
        if not request.allow_local_execution or not workspace:
            return None

        command = list(
            request.start_command
            or (framework.suggested_start_command if framework else [])
            or []
        )
        if not command:
            return None
        port = _available_port()
        command = _with_port(command, framework, port)
        if (
            command[0] in {"npm", "pnpm", "yarn", "bun", "npx"}
            and not (workspace / "node_modules").exists()
        ):
            if not request.allow_dependency_install:
                raise AccessPilotError(
                    "Node dependencies are absent; enable dependency installation or start the app externally and provide app_url."
                )
            install = (
                ["npm", "ci"]
                if (workspace / "package-lock.json").exists()
                else ["npm", "install"]
            )
            install_result = await self.supervisor.run(
                install,
                cwd=workspace,
                allow_install=True,
                timeout=600,
            )
            if install_result.exit_code != 0:
                raise AccessPilotError(
                    f"Dependency installation failed: {install_result.stderr or install_result.stdout}"
                )
        url = f"http://127.0.0.1:{port}/"
        await self.supervisor.start_server(
            run_id,
            command,
            cwd=workspace,
            url=url,
            allow_install=request.allow_dependency_install,
            extra_env={"PORT": str(port), "HOST": "127.0.0.1"},
        )
        return url


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _with_port(
    command: list[str], framework: FrameworkInfo | None, port: int
) -> list[str]:
    values = [str(port) if part == "{port}" else part for part in command]
    if "4173" in values:
        values = [str(port) if part == "4173" else part for part in values]
        return values
    if any(part in {"--port", "-p"} for part in values):
        return values
    kind = framework.kind if framework else FrameworkKind.UNKNOWN
    if values[:3] == ["python3", "-m", "http.server"] or values[:3] == [
        "python",
        "-m",
        "http.server",
    ]:
        return [*values, str(port), "--bind", "127.0.0.1"]
    if values[:2] == ["npm", "run"]:
        if kind == FrameworkKind.NEXTJS:
            return [*values, "--", "--hostname", "127.0.0.1", "--port", str(port)]
        return [*values, "--", "--host", "127.0.0.1", "--port", str(port)]
    if kind == FrameworkKind.ANGULAR:
        return [*values, "--host", "127.0.0.1", "--port", str(port)]
    return values


local_app_runner = LocalAppRunner()
