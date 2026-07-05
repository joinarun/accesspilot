"""Preview URL and artifact helpers."""

from __future__ import annotations

from pathlib import Path

from app.schemas import FrameworkKind, PreviewInfo, WorkflowState
from app.settings import AccessPilotSettings, get_settings

from .safety import safe_path, validate_http_url

_PREVIEW_SUFFIXES = {
    ".html",
    ".htm",
    ".css",
    ".js",
    ".mjs",
    ".svg",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".avif",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
}


class PreviewService:
    def __init__(self, settings: AccessPilotSettings | None = None) -> None:
        self.settings = settings or get_settings()

    def build(self, state: WorkflowState, base_url: str) -> PreviewInfo:
        base_url = validate_http_url(base_url).rstrip("/")
        baseline_screenshot = next(
            (
                scan.screenshot_path
                for scan in state.baseline_scans
                if scan.screenshot_path
            ),
            None,
        )
        fixed_screenshot = next(
            (
                scan.screenshot_path
                for scan in state.verification_scans
                if scan.screenshot_path
            ),
            None,
        )
        if state.request.demo_mode or state.request.target == "sample://vulnerable-app":
            original_url = f"{base_url}/demo/original/"
            fixed_url = f"{base_url}/api/previews/{state.run_id}/fixed/"
        elif (
            state.workspace()
            and state.framework
            and state.framework.kind == FrameworkKind.PLAIN_HTML
        ):
            original_url = state.app_url
            fixed_url = f"{base_url}/api/previews/{state.run_id}/fixed/"
        else:
            original_url = state.app_url
            fixed_url = state.app_url if state.changes else None
        return PreviewInfo(
            original_url=original_url,
            fixed_url=fixed_url,
            original_screenshot=self._artifact_url(state.run_id, baseline_screenshot),
            fixed_screenshot=self._artifact_url(state.run_id, fixed_screenshot),
        )

    def _artifact_url(self, run_id: str, value: str | None) -> str | None:
        if not value:
            return None
        path = Path(value).resolve()
        run_root = (self.settings.artifact_root / run_id).resolve()
        try:
            relative = path.relative_to(run_root).as_posix()
        except ValueError:
            return None
        return f"/api/artifacts/{run_id}/{relative}"

    def resolve_fixed_file(self, state: WorkflowState, relative: str) -> Path:
        if not state.workspace_path:
            raise FileNotFoundError("This run has no local preview")
        root = Path(state.workspace_path).resolve()
        candidate = relative or "index.html"
        path = safe_path(root, candidate)
        if path.is_dir():
            path = safe_path(root, path / "index.html")
        relative_path = path.relative_to(root)
        if (
            not path.is_file()
            or path.suffix.lower() not in _PREVIEW_SUFFIXES
            or any(part.startswith(".") for part in relative_path.parts)
        ):
            raise FileNotFoundError(candidate)
        return path

    def resolve_artifact(self, run_id: str, relative: str) -> Path:
        root = (self.settings.artifact_root / run_id).resolve()
        path = safe_path(root, relative)
        if not path.is_file():
            raise FileNotFoundError(relative)
        return path


preview_service = PreviewService()
