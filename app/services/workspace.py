"""Target classification and isolated source loading."""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from app.schemas import AccessPilotRequest, ResolvedTarget, TargetKind
from app.settings import AccessPilotSettings, get_settings

from .errors import AccessPilotError
from .safety import (
    is_github_repository_url,
    redact_text,
    sanitized_environment,
    validate_external_http_url,
    validate_http_url,
)

_COPY_IGNORE = shutil.ignore_patterns(
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    ".next",
    ".angular",
    "coverage",
    ".accesspilot",
    ".ssh",
    ".aws",
    ".gnupg",
    "__pycache__",
    "*.pyc",
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa*",
    "id_ed25519*",
)


class WorkspaceManager:
    """Create a run-scoped copy so user source is never edited in place."""

    def __init__(self, settings: AccessPilotSettings | None = None) -> None:
        self.settings = settings or get_settings()

    async def prepare(
        self, request: AccessPilotRequest, run_id: str
    ) -> tuple[ResolvedTarget, Path | None]:
        run_root = (self.settings.workspace_root / run_id).resolve()
        if run_root.exists():
            raise AccessPilotError(f"Run workspace already exists: {run_id}")
        run_root.mkdir(parents=True)

        if request.target == "sample://vulnerable-app" or request.demo_mode:
            destination = run_root / "repository"
            await asyncio.to_thread(
                shutil.copytree,
                self.settings.sample_root,
                destination,
                ignore=_COPY_IGNORE,
            )
            return (
                ResolvedTarget(
                    kind=TargetKind.LOCAL_REPOSITORY,
                    original="sample://vulnerable-app",
                    source_path=str(destination),
                    source_available=True,
                    isolated_copy=True,
                ),
                destination,
            )

        raw = request.target.strip()
        if is_github_repository_url(raw):
            normalized = self._normalize_github_url(raw)
            destination = run_root / "repository"
            await asyncio.to_thread(self._clone_remote, normalized, destination)
            return (
                ResolvedTarget(
                    kind=TargetKind.GITHUB_REPOSITORY,
                    original=raw,
                    remote_url=normalized,
                    source_path=str(destination),
                    source_available=True,
                    isolated_copy=True,
                    default_branch=self._current_branch(destination),
                ),
                destination,
            )

        candidate = Path(raw).expanduser()
        if candidate.exists():
            source = candidate.resolve()
            if not source.is_dir():
                raise AccessPilotError("Local target must be a directory")
            if not any(
                _is_within(source, allowed)
                for allowed in self.settings.allowed_source_root_paths
            ):
                raise AccessPilotError(
                    "Local target is outside ACCESSPILOT_ALLOWED_SOURCE_ROOTS"
                )
            if source in {Path(source.anchor), Path.home().resolve()}:
                raise AccessPilotError("Refusing to ingest a filesystem or home root")
            destination = run_root / "repository"
            if (source / ".git").exists():
                remote_url = self._github_origin(source)
                await asyncio.to_thread(self._clone_local, source, destination)
                if remote_url:
                    await asyncio.to_thread(
                        self._run_git,
                        ["git", "remote", "set-url", "origin", remote_url],
                        cwd=destination,
                        timeout=10,
                    )
                default_branch = self._current_branch(destination)
            else:
                remote_url = None
                await asyncio.to_thread(
                    shutil.copytree, source, destination, ignore=_COPY_IGNORE
                )
                default_branch = None
            return (
                ResolvedTarget(
                    kind=TargetKind.LOCAL_REPOSITORY,
                    original=raw,
                    remote_url=remote_url,
                    source_path=str(destination),
                    source_available=True,
                    isolated_copy=True,
                    default_branch=default_branch,
                ),
                destination,
            )

        url = validate_external_http_url(
            raw,
            allow_private=self.settings.allow_private_network_targets,
        )
        return (
            ResolvedTarget(
                kind=TargetKind.WEBSITE,
                original=raw,
                normalized_url=url,
                source_available=False,
                isolated_copy=False,
            ),
            None,
        )

    @staticmethod
    def _normalize_github_url(value: str) -> str:
        value = validate_http_url(value)
        parsed = urlsplit(value)
        path = parsed.path.rstrip("/")
        if path.endswith(".git"):
            path = path[:-4]
        parts = [part for part in path.split("/") if part]
        if len(parts) != 2:
            raise AccessPilotError(
                "GitHub target must identify one repository as owner/name"
            )
        return urlunsplit(
            ("https", "github.com", f"/{parts[0]}/{parts[1]}.git", "", "")
        )

    @staticmethod
    def _git_env() -> dict[str, str]:
        return sanitized_environment(
            {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
        )

    def _clone_remote(self, url: str, destination: Path) -> None:
        self._run_git(
            [
                "git",
                "clone",
                "--depth=1",
                "--filter=blob:limit=5m",
                "--no-tags",
                url,
                str(destination),
            ],
            cwd=destination.parent,
            timeout=180,
        )

    def _clone_local(self, source: Path, destination: Path) -> None:
        self._run_git(
            [
                "git",
                "clone",
                "--local",
                "--no-hardlinks",
                str(source),
                str(destination),
            ],
            cwd=destination.parent,
            timeout=180,
        )

    def _run_git(self, argv: list[str], *, cwd: Path, timeout: int) -> str:
        try:
            result = subprocess.run(
                argv,
                cwd=cwd,
                env=self._git_env(),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AccessPilotError(
                f"Git operation failed: {type(exc).__name__}"
            ) from exc
        if result.returncode != 0:
            detail = redact_text(result.stderr or result.stdout, limit=2000)
            raise AccessPilotError(f"Git operation failed: {detail}")
        return result.stdout

    def _current_branch(self, repository: Path) -> str | None:
        try:
            output = self._run_git(
                ["git", "branch", "--show-current"], cwd=repository, timeout=10
            ).strip()
        except AccessPilotError:
            return None
        return output or None

    def _github_origin(self, repository: Path) -> str | None:
        try:
            origin = self._run_git(
                ["git", "remote", "get-url", "origin"],
                cwd=repository,
                timeout=10,
            ).strip()
        except AccessPilotError:
            return None
        ssh_match = re.fullmatch(r"git@github\.com:([^/]+)/(.+?)(?:\.git)?", origin)
        if ssh_match:
            origin = f"https://github.com/{ssh_match.group(1)}/{ssh_match.group(2)}"
        if not is_github_repository_url(origin):
            return None
        return self._normalize_github_url(origin)


workspace_manager = WorkspaceManager()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
