"""Filesystem, subprocess, URL, and secret-safety primitives."""

from __future__ import annotations

import ipaddress
import os
import re
import socket
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from .errors import SafetyViolation

_SECRET_NAME = re.compile(
    r"(?:api[_-]?key|token|secret|password|passwd|credential|authorization|cookie)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
_LONG_TOKEN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_-]{40,}(?![A-Za-z0-9])")

_ALLOWED_EXECUTABLES = {
    "npm",
    "pnpm",
    "yarn",
    "bun",
    "node",
    "npx",
    "python",
    "python3",
    "uv",
    "poetry",
    "pytest",
    "ruff",
    "mypy",
    "ty",
    "vite",
    "next",
    "ng",
}
_INSTALL_WORDS = {"install", "add", "ci", "sync"}
_PROTECTED_PARTS = {
    ".git",
    ".hg",
    ".svn",
    ".ssh",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "coverage",
}
_PROTECTED_NAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
}


def redact_text(value: str, *, limit: int | None = None) -> str:
    """Best-effort redaction for command output and user-facing diagnostics."""
    redacted = _BEARER.sub("Bearer [REDACTED]", value)
    redacted = _JWT.sub("[REDACTED_JWT]", redacted)
    redacted = re.sub(
        r"(?im)^([^\n:=]*(?:api[_-]?key|token|secret|password|credential)[^\n:=]*[:=]\s*)([^\s]+)",
        r"\1[REDACTED]",
        redacted,
    )
    redacted = _LONG_TOKEN.sub("[REDACTED_TOKEN]", redacted)
    if limit is not None and len(redacted) > limit:
        redacted = f"{redacted[:limit]}\n… output truncated by AccessPilot …"
    return redacted


def sanitized_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build an environment for untrusted app commands without inherited secrets."""
    allowed_names = {
        "PATH",
        "HOME",
        "USER",
        "TMPDIR",
        "TEMP",
        "TMP",
        "LANG",
        "LC_ALL",
        "TERM",
        "SHELL",
        "SYSTEMROOT",
        "WINDIR",
    }
    env = {key: value for key, value in os.environ.items() if key in allowed_names}
    env.update(
        {
            "CI": "1",
            "NO_COLOR": "1",
            "npm_config_audit": "false",
            "npm_config_fund": "false",
            "npm_config_update_notifier": "false",
        }
    )
    if extra:
        for key, value in extra.items():
            if _SECRET_NAME.search(key):
                raise SafetyViolation(f"Refusing to pass secret-like variable {key!r}")
            env[key] = value
    return env


def validate_http_url(value: str) -> str:
    """Normalize an HTTP(S) URL and reject embedded credentials or fragments."""
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"}:
        raise SafetyViolation("Only http:// and https:// targets are supported")
    if not parsed.hostname:
        raise SafetyViolation("Target URL must include a hostname")
    if parsed.username or parsed.password:
        raise SafetyViolation("Credentials must not be embedded in a target URL")
    if any(char in value for char in ("\x00", "\r", "\n")):
        raise SafetyViolation("Target URL contains control characters")
    if any(_SECRET_NAME.search(key) and item for key, item in parse_qsl(parsed.query)):
        raise SafetyViolation("Credentials must not be placed in target URL queries")
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, "")
    )


def validate_external_http_url(value: str, *, allow_private: bool = False) -> str:
    """Validate an outbound scan target and reject private-network resolution."""
    normalized = validate_http_url(value)
    if allow_private:
        return normalized
    hostname = (urlsplit(normalized).hostname or "").rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise SafetyViolation("Private-network scan targets require server opt-in")
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        }
    except socket.gaierror as exc:
        raise SafetyViolation("Target hostname could not be resolved safely") from exc
    if not addresses:
        raise SafetyViolation("Target hostname did not resolve")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError as exc:
            raise SafetyViolation("Target resolved to an invalid address") from exc
        if not ip.is_global:
            raise SafetyViolation(
                "Private, loopback, and link-local targets are blocked"
            )
    return normalized


def is_github_repository_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and (parsed.hostname or "").lower() in {"github.com", "www.github.com"}
        and len([part for part in parsed.path.split("/") if part]) >= 2
    )


def safe_path(root: Path, candidate: Path | str, *, must_exist: bool = False) -> Path:
    """Resolve a path and prove that it remains inside ``root``."""
    root = root.resolve()
    path = Path(candidate)
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve(strict=must_exist)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise SafetyViolation(f"Path escapes the run workspace: {candidate}") from exc
    return resolved


def validate_editable_path(root: Path, candidate: Path | str) -> Path:
    """Return a workspace-confined source path that is safe to edit."""
    path = safe_path(root, candidate, must_exist=True)
    relative = path.relative_to(root)
    if any(part in _PROTECTED_PARTS for part in relative.parts):
        raise SafetyViolation(f"Protected path cannot be edited: {relative}")
    if path.name in _PROTECTED_NAMES or _SECRET_NAME.search(path.name):
        raise SafetyViolation(f"Secret or credential file cannot be edited: {relative}")
    if path.is_symlink() or not path.is_file():
        raise SafetyViolation(f"Only regular source files can be edited: {relative}")
    return path


def validate_command(argv: list[str], *, allow_install: bool = False) -> list[str]:
    """Validate a shell-free argv list against the local execution allow-list."""
    if not argv or len(argv) > 20:
        raise SafetyViolation("Command must contain between 1 and 20 arguments")
    if any(any(char in arg for char in ("\x00", "\r", "\n")) for arg in argv):
        raise SafetyViolation("Command contains control characters")
    executable = Path(argv[0]).name
    if executable not in _ALLOWED_EXECUTABLES:
        raise SafetyViolation(f"Executable {executable!r} is not allow-listed")
    if executable in {"python", "python3", "node"} and any(
        part in {"-c", "-e", "--eval", "--print", "-p"} for part in argv[1:]
    ):
        raise SafetyViolation("Inline interpreter evaluation is not permitted")
    lowered = {part.lower() for part in argv[1:3]}
    if lowered & _INSTALL_WORDS and not allow_install:
        raise SafetyViolation("Dependency installation requires explicit permission")
    if executable == "npx" and not allow_install and "--no-install" not in argv:
        argv = [argv[0], "--no-install", *argv[1:]]
    return argv
