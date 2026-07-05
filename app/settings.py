"""Runtime settings that do not contain provider credentials."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_ROOT = (
    PROJECT_ROOT
    if (PROJECT_ROOT / "pyproject.toml").is_file()
    else Path.cwd().resolve()
)


class AccessPilotSettings(BaseSettings):
    """Environment-backed operational limits for local prototype runs."""

    model_config = SettingsConfigDict(
        env_prefix="ACCESSPILOT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    workspace_root: Path = RUNTIME_ROOT / ".accesspilot" / "workspaces"
    artifact_root: Path = RUNTIME_ROOT / ".accesspilot" / "artifacts"
    max_repair_rounds: int = Field(default=3, ge=1, le=5)
    command_timeout_seconds: int = Field(default=180, ge=5, le=1800)
    scanner_timeout_seconds: int = Field(default=60, ge=5, le=300)
    scanner_node_executable: str = "node"
    max_source_file_bytes: int = Field(default=1_000_000, ge=1024, le=10_000_000)
    max_command_output_chars: int = Field(default=20_000, ge=1000, le=100_000)
    max_routes: int = Field(default=25, ge=1, le=100)
    sample_port: int = Field(default=8000, ge=1024, le=65535)
    enable_local_execution: bool = False
    enable_dependency_install: bool = False
    enable_remote_mutations: bool = False
    allow_remote_clients: bool = False
    allow_private_network_targets: bool = False
    allowed_source_roots: str = str(RUNTIME_ROOT)

    @field_validator("workspace_root", "artifact_root", mode="after")
    @classmethod
    def make_absolute(cls, value: Path) -> Path:
        if not value.is_absolute():
            value = RUNTIME_ROOT / value
        return value.resolve()

    @property
    def scanner_script(self) -> Path:
        return PROJECT_ROOT / "scanner" / "src" / "scan.mjs"

    @property
    def scanner_package_root(self) -> Path:
        return PROJECT_ROOT / "scanner"

    @property
    def sample_root(self) -> Path:
        return PROJECT_ROOT / "examples" / "vulnerable-app"

    @property
    def frontend_dist(self) -> Path:
        return PROJECT_ROOT / "frontend" / "dist"

    @property
    def allowed_source_root_paths(self) -> tuple[Path, ...]:
        roots = [
            Path(value.strip()).expanduser().resolve()
            for value in self.allowed_source_roots.split(os.pathsep)
            if value.strip()
        ]
        return tuple(roots or [RUNTIME_ROOT])

    def ensure_runtime_directories(self) -> None:
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.artifact_root.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> AccessPilotSettings:
    settings = AccessPilotSettings()
    settings.ensure_runtime_directories()
    return settings
