"""Hatch build hook that creates the browser UI from pinned Node inputs."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    """Build frontend assets before Hatch evaluates forced includes."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict) -> None:
        if self.target_name not in {"sdist", "wheel"}:
            return
        root = Path(self.root)
        frontend = root / "frontend"
        node = shutil.which("node")
        npm = shutil.which("npm")
        if not node or not npm:
            raise RuntimeError(
                "Building AccessPilot requires Node.js 20+ and npm so the "
                "frontend asset can be generated reproducibly."
            )
        node_version = subprocess.run(
            [node, "--version"],
            check=True,
            capture_output=True,
            text=True,
            env=_build_environment(),
        ).stdout.strip()
        match = re.fullmatch(r"v(\d+)(?:\.\d+){2}", node_version)
        if not match or int(match.group(1)) < 20:
            raise RuntimeError(
                f"Building AccessPilot requires Node.js 20+; found {node_version!r}."
            )
        subprocess.run(
            [npm, "ci"],
            cwd=frontend,
            check=True,
            env=_build_environment(),
        )
        subprocess.run(
            [npm, "run", "build"],
            cwd=frontend,
            check=True,
            env=_build_environment(),
        )


def _build_environment() -> dict[str, str]:
    allowed = {
        "PATH",
        "HOME",
        "USER",
        "TMPDIR",
        "TEMP",
        "TMP",
        "LANG",
        "LC_ALL",
        "SYSTEMROOT",
        "WINDIR",
    }
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    environment.update(
        {
            "CI": "1",
            "npm_config_audit": "false",
            "npm_config_fund": "false",
            "npm_config_update_notifier": "false",
        }
    )
    return environment
