from pathlib import Path

import pytest

from app.schemas import AccessPilotRequest
from app.services.errors import SafetyViolation
from app.services.safety import (
    redact_text,
    safe_path,
    validate_command,
    validate_external_http_url,
    validate_http_url,
)


def test_url_rejects_embedded_credentials() -> None:
    with pytest.raises(SafetyViolation, match="Credentials"):
        validate_http_url("https://user:secret@example.com/app")
    with pytest.raises(SafetyViolation, match="queries"):
        validate_http_url("https://example.com/app?access_token=secret")


def test_outbound_url_rejects_private_network_without_opt_in() -> None:
    with pytest.raises(SafetyViolation, match="Private"):
        validate_external_http_url("http://127.0.0.1/internal")
    assert (
        validate_external_http_url("http://127.0.0.1/internal", allow_private=True)
        == "http://127.0.0.1/internal"
    )


def test_path_cannot_escape_workspace(tmp_path: Path) -> None:
    with pytest.raises(SafetyViolation, match="escapes"):
        safe_path(tmp_path, "../outside.txt")


def test_command_blocks_shells_and_dependency_install_by_default() -> None:
    with pytest.raises(SafetyViolation, match="allow-listed"):
        validate_command(["bash", "-c", "echo unsafe"])
    with pytest.raises(SafetyViolation, match="explicit permission"):
        validate_command(["npm", "install"])
    with pytest.raises(SafetyViolation, match="Inline interpreter"):
        validate_command(["python", "-c", "print('unsafe')"])


def test_authentication_guidance_rejects_embedded_secrets() -> None:
    with pytest.raises(ValueError, match="describe a process"):
        AccessPilotRequest(
            target="https://example.com",
            authentication_instructions="password=super-secret-value",
        )


def test_redaction_removes_bearer_and_named_secret() -> None:
    value = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz\napi_key=supersecretvalue"
    redacted = redact_text(value)
    assert "abcdefghijklmnopqrstuvwxyz" not in redacted
    assert "supersecretvalue" not in redacted
    assert "REDACTED" in redacted
