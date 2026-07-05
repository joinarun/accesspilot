from pathlib import Path

import pytest

from app.schemas import AccessPilotRequest, WcagLevel, WorkflowState
from app.services.errors import SafetyViolation
from app.services.previews import PreviewService
from app.services.routes import discover_routes
from app.services.scanner import _source_checks


def test_route_discovery_rejects_cross_origin_explicit_route() -> None:
    request = AccessPilotRequest(
        target="https://example.com",
        routes=["https://attacker.example/path"],
        scan_depth=0,
    )
    with pytest.raises(SafetyViolation, match="same-origin"):
        discover_routes(request, None, None, "https://example.com/")


def test_fixed_preview_allows_web_assets_but_blocks_hidden_files(
    tmp_path: Path,
) -> None:
    (tmp_path / "index.html").write_text("<h1>Preview</h1>", encoding="utf-8")
    hidden = tmp_path / ".git"
    hidden.mkdir()
    (hidden / "config").write_text("secret metadata", encoding="utf-8")
    state = WorkflowState(
        request=AccessPilotRequest(target="sample://vulnerable-app"),
        workspace_path=str(tmp_path),
    )
    service = PreviewService()

    assert service.resolve_fixed_file(state, "").name == "index.html"
    with pytest.raises(FileNotFoundError):
        service.resolve_fixed_file(state, ".git/config")


def test_source_checks_respect_selected_wcag_level(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text(
        '<div id="action" onclick="run()">Run</div>', encoding="utf-8"
    )
    (tmp_path / "styles.css").write_text("*:focus { outline: none; }", encoding="utf-8")

    level_a = _source_checks(tmp_path, "/", wcag_level=WcagLevel.A)
    level_aa = _source_checks(tmp_path, "/", wcag_level=WcagLevel.AA)

    assert "non-semantic-interactive" in {item.id for item in level_a}
    assert "focus-visible" not in {item.id for item in level_a}
    assert "focus-visible" in {item.id for item in level_aa}
