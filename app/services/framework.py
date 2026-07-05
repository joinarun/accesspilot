"""Conservative framework and command detection from source manifests."""

from __future__ import annotations

import json
from pathlib import Path

from app.schemas import FrameworkInfo, FrameworkKind


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def detect_framework(root: Path) -> FrameworkInfo:
    manifests: list[str] = []
    evidence: list[str] = []
    commands: list[list[str]] = []
    package_json = root / "package.json"
    if package_json.exists():
        manifests.append("package.json")
        package = _read_json(package_json)
        dependencies = {
            **package.get("dependencies", {}),
            **package.get("devDependencies", {}),
        }
        scripts = package.get("scripts", {})
        if "next" in dependencies:
            kind, confidence = FrameworkKind.NEXTJS, 0.98
            evidence.append("package.json depends on next")
        elif "@angular/core" in dependencies:
            kind, confidence = FrameworkKind.ANGULAR, 0.98
            evidence.append("package.json depends on @angular/core")
        elif "vue" in dependencies:
            kind, confidence = FrameworkKind.VUE, 0.96
            evidence.append("package.json depends on vue")
        elif "svelte" in dependencies or "@sveltejs/kit" in dependencies:
            kind, confidence = FrameworkKind.SVELTE, 0.96
            evidence.append("package.json contains Svelte dependencies")
        elif "react" in dependencies:
            kind, confidence = FrameworkKind.REACT, 0.95
            evidence.append("package.json depends on react")
        else:
            kind, confidence = FrameworkKind.SERVER_RENDERED, 0.45
            evidence.append("Node project detected without a known UI framework")
        start = _node_start_command(scripts)
        for name in ("lint", "typecheck", "test", "build"):
            if name in scripts:
                commands.append(["npm", "run", name])
        return FrameworkInfo(
            kind=kind,
            confidence=confidence,
            manifests=manifests,
            evidence=evidence,
            suggested_start_command=start,
            suggested_build_command=["npm", "run", "build"]
            if "build" in scripts
            else None,
            suggested_test_commands=commands,
        )

    if (root / "angular.json").exists():
        return FrameworkInfo(
            kind=FrameworkKind.ANGULAR,
            confidence=0.9,
            manifests=["angular.json"],
            evidence=["angular.json exists"],
            suggested_start_command=["npx", "ng", "serve"],
        )
    if (root / "manage.py").exists():
        return FrameworkInfo(
            kind=FrameworkKind.DJANGO,
            confidence=0.9,
            manifests=["manage.py"],
            evidence=["Django manage.py exists"],
            suggested_start_command=["python", "manage.py", "runserver"],
            suggested_test_commands=[["python", "manage.py", "test"]],
        )
    if (root / "app.py").exists() and (root / "requirements.txt").exists():
        return FrameworkInfo(
            kind=FrameworkKind.FLASK,
            confidence=0.65,
            manifests=["requirements.txt", "app.py"],
            evidence=["Python application entry point detected"],
        )
    html_files = list(root.glob("*.html"))
    if html_files:
        return FrameworkInfo(
            kind=FrameworkKind.PLAIN_HTML,
            confidence=0.99,
            manifests=[path.name for path in html_files[:10]],
            evidence=["Top-level HTML files detected"],
            suggested_start_command=[
                "python3",
                "-m",
                "http.server",
                "4173",
                "--bind",
                "127.0.0.1",
            ],
        )
    return FrameworkInfo(
        kind=FrameworkKind.UNKNOWN,
        confidence=0.0,
        evidence=["No supported framework manifest was detected"],
    )


def _node_start_command(scripts: dict[str, str]) -> list[str] | None:
    for name in ("dev", "start", "serve"):
        if name in scripts:
            return ["npm", "run", name]
    return None
