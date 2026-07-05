"""Playwright/axe-core adapter plus deterministic source-level checks."""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from app.schemas import AxeNode, AxeViolation, PageScanResult, PageTarget, WcagLevel
from app.settings import AccessPilotSettings, get_settings

from .errors import AccessPilotError, ToolUnavailable
from .safety import redact_text, sanitized_environment


class AccessibilityScanner:
    def __init__(self, settings: AccessPilotSettings | None = None) -> None:
        self.settings = settings or get_settings()

    async def scan(
        self,
        pages: list[PageTarget],
        *,
        run_id: str,
        phase: str,
        workspace: Path | None = None,
        wcag_level: WcagLevel = WcagLevel.AA,
        allow_private_network: bool = False,
    ) -> list[PageScanResult]:
        if not pages:
            return []
        self._check_installation()
        screenshot_dir = self.settings.artifact_root / run_id / phase / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        argv = [
            self.settings.scanner_node_executable,
            str(self.settings.scanner_script),
            "--timeout",
            str(self.settings.scanner_timeout_seconds * 1000),
            "--screenshot-dir",
            str(screenshot_dir),
            "--wcag-level",
            wcag_level.value,
        ]
        for page in pages:
            argv.extend(["--url", page.url])
        if allow_private_network:
            argv.append("--allow-private-network")

        started = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(self.settings.scanner_package_root),
            env=sanitized_environment(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=(self.settings.scanner_timeout_seconds * len(pages)) + 20,
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise AccessPilotError(
                "Accessibility scanner exceeded its run timeout"
            ) from exc
        raw_stdout = stdout.decode(errors="replace")
        try:
            payload = json.loads(raw_stdout)
        except json.JSONDecodeError as exc:
            detail = redact_text(stderr.decode(errors="replace"), limit=2000)
            raise AccessPilotError(
                f"Accessibility scanner returned invalid JSON: {detail}"
            ) from exc
        if process.returncode not in {0, 1}:
            fatal = payload.get("fatalError") or {}
            message = redact_text(
                str(fatal.get("message") or "scanner failed"), limit=2000
            )
            raise AccessPilotError(f"Accessibility scanner failed: {message}")

        elapsed_ms = int((time.monotonic() - started) * 1000)
        by_url = {page.url: page for page in pages}
        results: list[PageScanResult] = []
        for item in payload.get("results", []):
            input_url = item.get("inputUrl") or item.get("url") or ""
            page = by_url.get(input_url) or next(
                (
                    candidate
                    for candidate in pages
                    if candidate.url.rstrip("/") == str(input_url).rstrip("/")
                ),
                PageTarget(route="/", url=input_url),
            )
            summary = item.get("summary") or {}
            error = item.get("error")
            violations = [
                _parse_violation(value) for value in item.get("violations", [])
            ]
            if workspace:
                violations.extend(
                    _source_checks(workspace, page.route, wcag_level=wcag_level)
                )
            results.append(
                PageScanResult(
                    url=item.get("url") or input_url,
                    route=page.route,
                    title=item.get("title"),
                    scanner=(payload.get("tool") or {}).get(
                        "engine", "axe-core/playwright"
                    ),
                    scanner_version=(payload.get("tool") or {}).get("version"),
                    duration_ms=elapsed_ms // max(len(pages), 1),
                    violations=_deduplicate_violations(violations),
                    passes=int(summary.get("passCount", 0)),
                    incomplete=int(summary.get("incompleteCount", 0)),
                    inapplicable=int(summary.get("inapplicableCount", 0)),
                    screenshot_path=item.get("screenshotPath"),
                    error=redact_text(str(error.get("message")), limit=1000)
                    if isinstance(error, dict)
                    else None,
                )
            )
        if not results:
            raise AccessPilotError("Accessibility scanner produced no page results")
        return results

    def _check_installation(self) -> None:
        if not self.settings.scanner_script.exists():
            raise ToolUnavailable("The bundled axe scanner script is missing")
        package = self.settings.scanner_package_root
        if not (package / "node_modules" / "@axe-core" / "playwright").exists():
            raise ToolUnavailable(
                "Scanner dependencies are not installed. Run `npm install` in scanner/."
            )


def _parse_violation(value: dict[str, Any]) -> AxeViolation:
    return AxeViolation(
        id=str(value.get("id", "unknown")),
        impact=value.get("impact"),
        tags=[str(tag) for tag in value.get("tags", [])],
        description=str(value.get("description", "")),
        help=str(value.get("help", "")),
        help_url=value.get("helpUrl"),
        nodes=[
            AxeNode(
                html=redact_text(str(node.get("html", "")), limit=2000),
                target=_flatten_target(node.get("target", [])),
                failure_summary=node.get("failureSummary"),
                impact=node.get("impact") or value.get("impact"),
            )
            for node in value.get("nodes", [])
        ],
    )


def _flatten_target(value: Any) -> list[str]:
    flattened: list[str] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, list):
                flattened.extend(_flatten_target(item))
            else:
                flattened.append(str(item))
    elif value is not None:
        flattened.append(str(value))
    return flattened


def _source_checks(
    root: Path,
    route: str,
    *,
    wcag_level: WcagLevel,
) -> list[AxeViolation]:
    html_paths = sorted(root.glob("*.html"))
    if not html_paths:
        return []
    selected = root / (route.lstrip("/") or "index.html")
    if selected.suffix.lower() not in {".html", ".htm"}:
        selected = selected.with_suffix(".html")
    if not selected.exists():
        selected = root / "index.html"
    try:
        text = selected.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    soup = BeautifulSoup(text, "html.parser")
    findings: list[AxeViolation] = []
    interactive_nodes: list[AxeNode] = []
    for element in soup.find_all(attrs={"onclick": True}):
        if element.name not in {"button", "a", "input", "select", "textarea"}:
            selector = f"#{element.get('id')}" if element.get("id") else element.name
            interactive_nodes.append(
                AxeNode(
                    html=redact_text(str(element)[:2000]),
                    target=[selector],
                    impact="serious",
                    failure_summary="A pointer-clickable generic element is not keyboard operable.",
                )
            )
    javascript = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in root.glob("*.js")
        if path.is_file() and path.stat().st_size < 1_000_000
    )
    assignments = {}
    for match in re.finditer(
        r"(?:const|let|var)\s+(\w+)\s*=\s*document\.(?:querySelector\(\s*[\"']#([^\"']+)[\"']\s*\)|getElementById\(\s*[\"']([^\"']+)[\"']\s*\))",
        javascript,
    ):
        assignments[match.group(1)] = match.group(2) or match.group(3)
    click_variables = set(
        re.findall(r"\b(\w+)\.addEventListener\(\s*[\"']click[\"']", javascript)
    )
    if click_variables:
        for variable in click_variables:
            element_id = assignments.get(variable)
            if not element_id:
                continue
            element = soup.find(id=element_id)
            if not element or element.name in {
                "button",
                "a",
                "input",
                "select",
                "textarea",
            }:
                continue
            node = AxeNode(
                html=redact_text(str(element)[:2000]),
                target=[f"#{element_id}"],
                impact="serious",
                failure_summary="A script attaches click behavior to a generic element without native keyboard semantics.",
            )
            if all(node.html != existing.html for existing in interactive_nodes):
                interactive_nodes.append(node)
    if interactive_nodes:
        findings.append(
            AxeViolation(
                id="non-semantic-interactive",
                impact="serious",
                tags=["wcag2a", "wcag211", "wcag412"],
                description="Interactive controls must use keyboard-operable native semantics.",
                help="Use native buttons and links for interactive behavior",
                nodes=interactive_nodes,
            )
        )

    error_nodes: list[AxeNode] = []
    for element in soup.select(".error, .error-message, [data-error]"):
        if not element.get("role") and not element.get("aria-live"):
            selector = (
                f"#{element.get('id')}" if element.get("id") else ".error-message"
            )
            error_nodes.append(
                AxeNode(
                    html=redact_text(str(element)[:2000]),
                    target=[selector],
                    impact="serious",
                    failure_summary="Dynamic validation feedback is not announced to assistive technology.",
                )
            )
    if error_nodes:
        findings.append(
            AxeViolation(
                id="accessible-error-message",
                impact="serious",
                tags=["wcag2a", "wcag331", "wcag413"],
                description="Validation feedback must be associated and announced.",
                help="Associate and announce dynamic error messages",
                nodes=error_nodes,
            )
        )

    css_nodes: list[AxeNode] = []
    for css_path in root.glob("*.css"):
        try:
            css = css_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in re.finditer(
            r"([^{}]*:focus[^{}]*)\{[^{}]*outline\s*:\s*none", css, re.I
        ):
            selector = match.group(1).strip() or ":focus"
            css_nodes.append(
                AxeNode(
                    html=f"/* {css_path.name}: {selector} */",
                    target=[selector],
                    impact="serious",
                    failure_summary="The stylesheet suppresses the keyboard focus indicator.",
                )
            )
    if css_nodes and wcag_level in {WcagLevel.AA, WcagLevel.AAA}:
        findings.append(
            AxeViolation(
                id="focus-visible",
                impact="serious",
                tags=["wcag2aa", "wcag247", "wcag2411"],
                description="Keyboard focus must remain visually apparent.",
                help="Provide a visible keyboard focus indicator",
                nodes=css_nodes,
            )
        )
    return findings


def _deduplicate_violations(values: list[AxeViolation]) -> list[AxeViolation]:
    grouped: dict[str, AxeViolation] = {}
    for violation in values:
        existing = grouped.get(violation.id)
        if existing is None:
            grouped[violation.id] = violation
            continue
        seen = {(tuple(node.target), node.html) for node in existing.nodes}
        existing.nodes.extend(
            node
            for node in violation.nodes
            if (tuple(node.target), node.html) not in seen
        )
    return sorted(grouped.values(), key=lambda violation: violation.id)


accessibility_scanner = AccessibilityScanner()
