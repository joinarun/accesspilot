"""Static route discovery with bounded source inspection."""

from __future__ import annotations

import re
from collections import deque
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from app.schemas import AccessPilotRequest, FrameworkInfo, FrameworkKind, PageTarget

from .errors import SafetyViolation
from .safety import validate_http_url

_ROUTE_PATTERN = re.compile(r"(?:path\s*=\s*[\"']|path\s*:\s*[\"'])(/[^\"'{}]*)[\"']")


def discover_routes(
    request: AccessPilotRequest,
    framework: FrameworkInfo | None,
    workspace: Path | None,
    base_url: str,
    *,
    limit: int = 25,
    allow_network_crawl: bool = False,
) -> list[PageTarget]:
    routes = list(request.routes)
    if not routes and workspace:
        routes.extend(_source_routes(workspace, framework))
    if not routes:
        routes.append("/")
    if request.scan_depth > 0 and allow_network_crawl:
        routes.extend(_crawl_routes(base_url, request.scan_depth, limit))

    pages: list[PageTarget] = []
    seen: set[str] = set()
    for route in routes:
        route = route.strip()
        if route.startswith(("http://", "https://")):
            url = route
        elif request.demo_mode or request.target == "sample://vulnerable-app":
            url = (
                base_url
                if route == "/"
                else urljoin(f"{base_url.rstrip('/')}/", route.lstrip("/"))
            )
        else:
            url = urljoin(base_url, route)
        url = validate_http_url(url)
        if not _same_origin(base_url, url):
            raise SafetyViolation(
                "Discovered and explicit routes must remain same-origin"
            )
        canonical = url.rstrip("/") or url
        if canonical in seen:
            continue
        seen.add(canonical)
        pages.append(PageTarget(route=route, url=url))
        if len(pages) >= limit:
            break
    return pages


def _crawl_routes(base_url: str, max_depth: int, limit: int) -> list[str]:
    """Discover bounded same-origin links without following cross-origin redirects."""
    queue: deque[tuple[str, int]] = deque([(base_url, 0)])
    visited: set[str] = set()
    routes: list[str] = []
    try:
        with httpx.Client(
            follow_redirects=False,
            timeout=3.0,
            trust_env=False,
            headers={"User-Agent": "AccessPilot route discovery"},
        ) as client:
            while queue and len(routes) < limit:
                url, depth = queue.popleft()
                canonical = url.split("#", 1)[0].rstrip("/") or url
                if canonical in visited or not _same_origin(base_url, url):
                    continue
                visited.add(canonical)
                routes.append(url)
                if depth >= max_depth:
                    continue
                response = client.get(url)
                if response.status_code >= 300:
                    continue
                if "text/html" not in response.headers.get("content-type", ""):
                    continue
                body = response.text[:1_000_000]
                soup = BeautifulSoup(body, "html.parser")
                for anchor in soup.find_all("a", href=True):
                    href = str(anchor.get("href", "")).strip()
                    if not href or href.startswith(
                        ("#", "mailto:", "tel:", "javascript:")
                    ):
                        continue
                    candidate = urljoin(url, href).split("#", 1)[0]
                    try:
                        candidate = validate_http_url(candidate)
                    except SafetyViolation:
                        continue
                    if _same_origin(base_url, candidate):
                        queue.append((candidate, depth + 1))
    except (httpx.HTTPError, UnicodeError, ValueError):
        return routes
    return routes


def _same_origin(left: str, right: str) -> bool:
    first = urlsplit(left)
    second = urlsplit(right)
    return (
        first.scheme.lower(),
        (first.hostname or "").lower(),
        first.port or (443 if first.scheme == "https" else 80),
    ) == (
        second.scheme.lower(),
        (second.hostname or "").lower(),
        second.port or (443 if second.scheme == "https" else 80),
    )


def _source_routes(root: Path, framework: FrameworkInfo | None) -> list[str]:
    kind = framework.kind if framework else FrameworkKind.UNKNOWN
    if kind == FrameworkKind.PLAIN_HTML:
        routes: list[str] = []
        for path in sorted(root.glob("*.html")):
            routes.append("/" if path.name == "index.html" else f"/{path.name}")
        return routes
    if kind == FrameworkKind.NEXTJS:
        return _next_routes(root)

    routes: list[str] = []
    for path in list(root.rglob("*.tsx"))[:100] + list(root.rglob("*.jsx"))[:100]:
        if any(
            part in {"node_modules", "dist", "build", ".next"} for part in path.parts
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in _ROUTE_PATTERN.finditer(text[:200_000]):
            route = match.group(1)
            if route not in routes and ":" not in route and "*" not in route:
                routes.append(route)
    return routes


def _next_routes(root: Path) -> list[str]:
    routes: list[str] = []
    for base in (
        root / "app",
        root / "src" / "app",
        root / "pages",
        root / "src" / "pages",
    ):
        if not base.exists():
            continue
        for path in list(base.rglob("page.*")) + list(base.rglob("*.tsx")):
            relative = path.relative_to(base)
            if any(part.startswith(("[", "_")) for part in relative.parts):
                continue
            parts = list(relative.parts[:-1])
            if not parts and path.stem not in {"page", "index"}:
                parts = [path.stem]
            route = "/" + "/".join(parts)
            route = route.replace("/(", "/").replace(")", "")
            if route not in routes:
                routes.append(route)
    return routes
