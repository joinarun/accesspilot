"""Bounded, shell-free subprocess execution for inspected applications."""

from __future__ import annotations

import asyncio
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.schemas import CommandResult
from app.settings import AccessPilotSettings, get_settings

from .errors import AccessPilotError
from .safety import redact_text, sanitized_environment, validate_command


@dataclass
class ManagedProcess:
    run_id: str
    argv: list[str]
    cwd: Path
    process: asyncio.subprocess.Process
    url: str


class ProcessSupervisor:
    """Own child processes and guarantee bounded execution and cleanup."""

    def __init__(self, settings: AccessPilotSettings | None = None) -> None:
        self.settings = settings or get_settings()
        self._processes: dict[str, ManagedProcess] = {}
        self._lock = asyncio.Lock()

    async def run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        timeout: int | None = None,
        allow_install: bool = False,
        extra_env: dict[str, str] | None = None,
    ) -> CommandResult:
        safe_argv = validate_command(list(argv), allow_install=allow_install)
        started = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            *safe_argv,
            cwd=str(cwd),
            env=sanitized_environment(extra_env),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name != "nt",
        )
        timed_out = False
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout or self.settings.command_timeout_seconds,
            )
        except TimeoutError:
            timed_out = True
            await self._terminate(process)
            stdout_bytes, stderr_bytes = await process.communicate()
        limit = self.settings.max_command_output_chars
        return CommandResult(
            argv=safe_argv,
            cwd=str(cwd),
            exit_code=process.returncode,
            duration_ms=int((time.monotonic() - started) * 1000),
            stdout=redact_text(stdout_bytes.decode(errors="replace"), limit=limit),
            stderr=redact_text(stderr_bytes.decode(errors="replace"), limit=limit),
            timed_out=timed_out,
        )

    async def start_server(
        self,
        run_id: str,
        argv: list[str],
        *,
        cwd: Path,
        url: str,
        allow_install: bool = False,
        extra_env: dict[str, str] | None = None,
        readiness_timeout: int = 45,
    ) -> ManagedProcess:
        safe_argv = validate_command(list(argv), allow_install=allow_install)
        async with self._lock:
            await self.stop(run_id)
            process = await asyncio.create_subprocess_exec(
                *safe_argv,
                cwd=str(cwd),
                env=sanitized_environment(extra_env),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=os.name != "nt",
            )
            managed = ManagedProcess(run_id, safe_argv, cwd, process, url)
            self._processes[run_id] = managed

        deadline = time.monotonic() + readiness_timeout
        async with httpx.AsyncClient(follow_redirects=True, timeout=2.0) as client:
            while time.monotonic() < deadline:
                if process.returncode is not None:
                    stdout, stderr = await process.communicate()
                    detail = redact_text(
                        (stderr or stdout).decode(errors="replace"), limit=2000
                    )
                    self._processes.pop(run_id, None)
                    raise AccessPilotError(
                        f"Local app exited before it became ready: {detail}"
                    )
                try:
                    response = await client.get(url)
                    if response.status_code < 500:
                        return managed
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.25)
        await self.stop(run_id)
        raise AccessPilotError(
            f"Local app did not become ready within {readiness_timeout} seconds"
        )

    async def stop(self, run_id: str) -> None:
        managed = self._processes.pop(run_id, None)
        if managed and managed.process.returncode is None:
            await self._terminate(managed.process)

    async def stop_all(self) -> None:
        for run_id in list(self._processes):
            await self.stop(run_id)

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            if os.name != "nt" and process.pid:
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            await asyncio.wait_for(process.wait(), timeout=5)
        except (ProcessLookupError, TimeoutError):
            if process.returncode is None:
                try:
                    if os.name != "nt" and process.pid:
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                except ProcessLookupError:
                    return
                await process.wait()


process_supervisor = ProcessSupervisor()
