"""Configuration models for AccessPilot's supported LLM providers.

The models store environment-variable *names*, not credential values. Credential
lookup is deliberately deferred to the model factory so configuring one provider
does not require credentials for the other provider.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class LLMProvider(StrEnum):
    """LLM providers supported by AccessPilot."""

    GEMINI = "gemini"
    OPENAI = "openai"


class _ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("model", check_fields=False)
    @classmethod
    def _validate_model(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("model must not be empty")
        return value

    @field_validator("api_key_env", check_fields=False)
    @classmethod
    def _validate_api_key_env(cls, value: str) -> str:
        return _validate_env_name(value)


class GeminiProviderConfig(_ProviderConfig):
    """Native ADK Gemini configuration.

    ``auth_mode='auto'`` accepts either the configured API-key environment
    variable or a Vertex AI environment. No credential value is copied into this
    model.
    """

    model: str = "gemini-2.5-pro"
    api_key_env: str = "GOOGLE_API_KEY"
    auth_mode: Literal["auto", "api_key", "vertex_ai"] = "auto"
    retry_attempts: int = Field(default=3, ge=1, le=10)

    @field_validator("api_key_env")
    @classmethod
    def _validate_native_api_key_env(cls, value: str) -> str:
        if value not in {"GOOGLE_API_KEY", "GEMINI_API_KEY"}:
            raise ValueError("native Gemini supports GOOGLE_API_KEY or GEMINI_API_KEY")
        return value


class OpenAIProxyProviderConfig(_ProviderConfig):
    """OpenAI configuration restricted to an enterprise LLMproxy endpoint."""

    model: str = "gpt-4.1"
    base_url_env: str = "LLMPROXY_BASE_URL"
    api_key_env: str = "LLMPROXY_API_KEY"
    request_timeout_seconds: float = Field(default=120.0, gt=0, le=600)

    @field_validator("base_url_env")
    @classmethod
    def _validate_base_url_env(cls, value: str) -> str:
        return _validate_env_name(value)


class LLMConfig(BaseModel):
    """Provider selection plus provider-specific configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: LLMProvider = LLMProvider.GEMINI
    gemini: GeminiProviderConfig = Field(default_factory=GeminiProviderConfig)
    openai: OpenAIProxyProviderConfig = Field(default_factory=OpenAIProxyProviderConfig)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> LLMConfig:
        """Load non-secret provider settings from environment variables.

        Credential values and the proxy URL are intentionally not read here.
        They are resolved only if their provider is selected by
        :func:`app.providers.create_llm_model`.
        """

        source = os.environ if environ is None else environ
        return cls(
            provider=source.get("ACCESSPILOT_LLM_PROVIDER", "gemini"),
            gemini=GeminiProviderConfig(
                model=source.get(
                    "ACCESSPILOT_GEMINI_MODEL",
                    GeminiProviderConfig.model_fields["model"].default,
                ),
                api_key_env=source.get(
                    "ACCESSPILOT_GEMINI_API_KEY_ENV",
                    GeminiProviderConfig.model_fields["api_key_env"].default,
                ),
                auth_mode=source.get("ACCESSPILOT_GEMINI_AUTH_MODE", "auto"),
                retry_attempts=_read_int(
                    source,
                    "ACCESSPILOT_GEMINI_RETRY_ATTEMPTS",
                    GeminiProviderConfig.model_fields["retry_attempts"].default,
                ),
            ),
            openai=OpenAIProxyProviderConfig(
                model=source.get(
                    "ACCESSPILOT_OPENAI_MODEL",
                    OpenAIProxyProviderConfig.model_fields["model"].default,
                ),
                base_url_env=source.get(
                    "ACCESSPILOT_LLMPROXY_BASE_URL_ENV",
                    OpenAIProxyProviderConfig.model_fields["base_url_env"].default,
                ),
                api_key_env=source.get(
                    "ACCESSPILOT_LLMPROXY_API_KEY_ENV",
                    OpenAIProxyProviderConfig.model_fields["api_key_env"].default,
                ),
                request_timeout_seconds=_read_float(
                    source,
                    "ACCESSPILOT_OPENAI_TIMEOUT_SECONDS",
                    OpenAIProxyProviderConfig.model_fields[
                        "request_timeout_seconds"
                    ].default,
                ),
            ),
        )


def _validate_env_name(value: str) -> str:
    value = value.strip()
    if not _ENV_NAME.fullmatch(value):
        raise ValueError("must be a valid environment-variable name")
    return value


def _read_int(source: Mapping[str, str], name: str, default: int) -> int:
    raw_value = source.get(name)
    return default if raw_value is None else int(raw_value)


def _read_float(source: Mapping[str, str], name: str, default: float) -> float:
    raw_value = source.get(name)
    return default if raw_value is None else float(raw_value)


__all__ = [
    "GeminiProviderConfig",
    "LLMConfig",
    "LLMProvider",
    "OpenAIProxyProviderConfig",
]
