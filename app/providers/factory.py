"""Safe ADK model construction for AccessPilot."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit

from google.adk.models import Gemini
from google.adk.models.base_llm import BaseLlm
from google.genai import types

from .config import (
    GeminiProviderConfig,
    LLMConfig,
    LLMProvider,
    OpenAIProxyProviderConfig,
)

logger = logging.getLogger("accesspilot.ai")


class ProviderConfigurationError(ValueError):
    """Raised when selected-provider configuration is missing or unsafe."""


class ProviderDependencyError(RuntimeError):
    """Raised when an optional selected-provider dependency is unavailable."""


def create_llm_model(
    config: LLMConfig | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> BaseLlm:
    """Create the ADK model for the selected provider.

    Validation is lazy: only the selected provider's credentials and endpoint
    are inspected. Error messages identify environment-variable names but never
    include credential values.
    """

    source = os.environ if environ is None else environ
    resolved_config = config or LLMConfig.from_env(source)

    if resolved_config.provider is LLMProvider.GEMINI:
        return _create_gemini_model(resolved_config.gemini, source)
    if resolved_config.provider is LLMProvider.OPENAI:
        return _create_openai_proxy_model(resolved_config.openai, source)
    raise ProviderConfigurationError(
        f"Unsupported LLM provider: {resolved_config.provider!s}"
    )


def _create_gemini_model(
    config: GeminiProviderConfig, source: Mapping[str, str]
) -> Gemini:
    _validate_gemini_credentials(config, source)
    logger.info(
        "event=ai_model_configured provider=gemini model=%s transport=native_adk",
        config.model,
    )
    return Gemini(
        model=config.model,
        retry_options=types.HttpRetryOptions(attempts=config.retry_attempts),
    )


def _create_openai_proxy_model(
    config: OpenAIProxyProviderConfig, source: Mapping[str, str]
) -> BaseLlm:
    proxy_url = _validate_proxy_url(
        _require_env(source, config.base_url_env, "LLMproxy base URL")
    )
    api_key = _require_env(source, config.api_key_env, "LLMproxy API key")
    model_name = _normalize_openai_model(config.model)

    try:
        from google.adk.models.lite_llm import LiteLlm
    except ImportError as exc:
        raise ProviderDependencyError(
            "OpenAI through LLMproxy requires the optional LiteLLM integration; "
            "install the project with google-adk[extensions]."
        ) from exc

    logger.info(
        "event=ai_model_configured provider=openai model=%s "
        "transport=enterprise_llmproxy",
        config.model.removeprefix("openai/"),
    )
    return LiteLlm(
        model=model_name,
        api_base=proxy_url,
        api_key=api_key,
        timeout=config.request_timeout_seconds,
    )


def _validate_gemini_credentials(
    config: GeminiProviderConfig, source: Mapping[str, str]
) -> None:
    native_key_names = tuple(
        dict.fromkeys((config.api_key_env, "GOOGLE_API_KEY", "GEMINI_API_KEY"))
    )
    if config.auth_mode == "api_key":
        if not any(_has_env(source, name) for name in native_key_names):
            raise ProviderConfigurationError(
                "Gemini API key is unset. Set GOOGLE_API_KEY or GEMINI_API_KEY."
            )
        return

    vertex_enabled = _is_truthy(source.get("GOOGLE_GENAI_USE_VERTEXAI"))
    if config.auth_mode == "vertex_ai":
        if not vertex_enabled:
            raise ProviderConfigurationError(
                "Gemini auth_mode is vertex_ai, but "
                "GOOGLE_GENAI_USE_VERTEXAI is not enabled."
            )
        return

    if any(_has_env(source, name) for name in native_key_names) or vertex_enabled:
        return
    raise ProviderConfigurationError(
        "Gemini credentials are not configured. Set GOOGLE_API_KEY or "
        "GEMINI_API_KEY, or enable GOOGLE_GENAI_USE_VERTEXAI."
    )


def _validate_proxy_url(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError as exc:
        raise ProviderConfigurationError("LLMproxy base URL is invalid.") from exc

    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ProviderConfigurationError(
            "LLMproxy base URL must be an absolute HTTP(S) URL."
        )
    if parsed.username is not None or parsed.password is not None:
        raise ProviderConfigurationError(
            "LLMproxy base URL must not contain embedded credentials."
        )

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "openai.com" or hostname.endswith(".openai.com"):
        raise ProviderConfigurationError(
            "Direct public OpenAI endpoints are prohibited; configure the "
            "enterprise LLMproxy base URL."
        )
    if parsed.query or parsed.fragment:
        raise ProviderConfigurationError(
            "LLMproxy base URL must not contain a query string or fragment."
        )

    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    netloc = f"{host}:{port}" if port is not None else host
    normalized_path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, netloc, normalized_path, "", ""))


def _normalize_openai_model(model: str) -> str:
    value = model.strip()
    if "/" not in value:
        return f"openai/{value}"
    provider, model_name = value.split("/", 1)
    if provider.lower() != "openai" or not model_name:
        raise ProviderConfigurationError(
            "The LLMproxy model must be an OpenAI model name or use the "
            "'openai/' LiteLLM prefix."
        )
    return f"openai/{model_name}"


def _require_env(source: Mapping[str, str], name: str, label: str) -> str:
    value = source.get(name)
    if value is None or not value.strip():
        raise ProviderConfigurationError(
            f"{label} environment variable {name} is unset."
        )
    return value.strip()


def _has_env(source: Mapping[str, str], name: str) -> bool:
    value = source.get(name)
    return value is not None and bool(value.strip())


def _is_truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


__all__ = [
    "ProviderConfigurationError",
    "ProviderDependencyError",
    "create_llm_model",
]
