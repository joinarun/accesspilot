from __future__ import annotations

from types import SimpleNamespace

import pytest
from google.adk.models.llm_response import LlmResponse
from pydantic import ValidationError

from app.providers import (
    GeminiProviderConfig,
    LLMConfig,
    LLMProvider,
    OpenAIProxyProviderConfig,
    ProviderConfigurationError,
    create_llm_model,
)
from app.specialists import (
    AuditorOutput,
    InvestigatorOutput,
    PlannerOutput,
    PullRequestDraft,
    RepairerOutput,
    VerifierOutput,
    create_specialist_agents,
)


def test_gemini_selection_does_not_require_openai_configuration() -> None:
    model = create_llm_model(
        LLMConfig(provider=LLMProvider.GEMINI),
        environ={"GOOGLE_API_KEY": "test-only-key"},
    )

    assert model.model == "gemini-2.5-pro"


def test_gemini_accepts_native_gemini_api_key_fallback() -> None:
    model = create_llm_model(
        LLMConfig(provider=LLMProvider.GEMINI),
        environ={"GEMINI_API_KEY": "test-only-key"},
    )

    assert model.model == "gemini-2.5-pro"


def test_openai_rejects_public_endpoint_without_leaking_key() -> None:
    secret = "do-not-print-this-key"

    with pytest.raises(ProviderConfigurationError) as exc_info:
        create_llm_model(
            LLMConfig(provider=LLMProvider.OPENAI),
            environ={
                "LLMPROXY_BASE_URL": "https://api.openai.com/v1",
                "LLMPROXY_API_KEY": secret,
            },
        )

    assert "Direct public OpenAI endpoints are prohibited" in str(exc_info.value)
    assert secret not in str(exc_info.value)


def test_openai_builds_litellm_model_for_enterprise_proxy(caplog) -> None:
    pytest.importorskip("litellm")
    secret = "test-only-proxy-key"
    caplog.set_level("INFO", logger="accesspilot.ai")

    model = create_llm_model(
        LLMConfig(
            provider=LLMProvider.OPENAI,
            openai=OpenAIProxyProviderConfig(model="openai/gpt-4.1"),
        ),
        environ={
            "LLMPROXY_BASE_URL": "https://llmproxy.example.test/v1/",
            "LLMPROXY_API_KEY": secret,
        },
    )

    assert model.model == "openai/gpt-4.1"
    assert model._additional_args["api_base"] == "https://llmproxy.example.test/v1"
    assert model._additional_args["api_key"] == secret
    assert secret not in repr(model)
    assert (
        "event=ai_model_configured provider=openai model=gpt-4.1 "
        "transport=enterprise_llmproxy" in caplog.text
    )
    assert secret not in caplog.text


def test_environment_loader_reads_provider_metadata() -> None:
    config = LLMConfig.from_env(
        {
            "ACCESSPILOT_LLM_PROVIDER": "openai",
            "ACCESSPILOT_OPENAI_MODEL": "gpt-4.1-mini",
            "ACCESSPILOT_LLMPROXY_BASE_URL_ENV": "CORP_PROXY_URL",
            "ACCESSPILOT_LLMPROXY_API_KEY_ENV": "CORP_PROXY_KEY",
        }
    )

    assert config.provider is LLMProvider.OPENAI
    assert config.openai.model == "gpt-4.1-mini"
    assert config.openai.base_url_env == "CORP_PROXY_URL"
    assert config.openai.api_key_env == "CORP_PROXY_KEY"


def test_gemini_rejects_api_key_name_native_client_cannot_read() -> None:
    with pytest.raises(ValueError, match="GOOGLE_API_KEY or GEMINI_API_KEY"):
        GeminiProviderConfig(api_key_env="CUSTOM_GEMINI_SECRET")


def test_specialist_factories_return_fresh_static_graph_agents() -> None:
    first = create_specialist_agents("gemini-2.5-pro")
    second = create_specialist_agents("gemini-2.5-pro")

    assert set(first) == {
        "auditor",
        "investigator",
        "planner",
        "repairer",
        "verifier",
        "pr_writer",
    }
    assert all(first[name] is not second[name] for name in first)
    assert all(agent.mode == "single_turn" for agent in first.values())
    assert all(agent.tools == [] for agent in first.values())
    assert all(agent.output_schema is not None for agent in first.values())


@pytest.mark.parametrize(
    "output_schema",
    [
        AuditorOutput,
        InvestigatorOutput,
        PlannerOutput,
        RepairerOutput,
        VerifierOutput,
        PullRequestDraft,
    ],
)
def test_specialist_schema_is_gemini_compatible(output_schema) -> None:
    schema = output_schema.model_json_schema()

    def contains_additional_properties(value) -> bool:
        if isinstance(value, dict):
            return "additionalProperties" in value or any(
                contains_additional_properties(item) for item in value.values()
            )
        if isinstance(value, list):
            return any(contains_additional_properties(item) for item in value)
        return False

    assert contains_additional_properties(schema) is False


def test_specialist_runtime_validation_still_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AuditorOutput.model_validate(
            {"issues": [], "caveats": [], "unexpected": "not allowed"}
        )


@pytest.mark.asyncio
async def test_specialist_model_callbacks_log_safe_provenance(caplog) -> None:
    caplog.set_level("INFO", logger="accesspilot.ai")
    agent = create_specialist_agents("openai/gpt-4.1")["auditor"]
    context = SimpleNamespace(
        state={"workflow_state": {"run_id": "run-123"}},
        session=SimpleNamespace(id="session-123"),
        run_id="node-run-1",
        invocation_id="invocation-123",
    )

    await agent.before_model_callback(
        callback_context=context,
        llm_request=object(),
    )
    await agent.after_model_callback(
        callback_context=context,
        llm_response=LlmResponse(model_version="gpt-4.1-2026-01-01"),
    )
    await agent.on_model_error_callback(
        callback_context=context,
        llm_request=object(),
        error=RuntimeError("sensitive upstream response"),
    )

    assert (
        "event=ai_model_request run_id=run-123 provider=openai model=gpt-4.1 "
        "specialist=accessibility_auditor invocation_id=invocation-123" in caplog.text
    )
    assert (
        "event=ai_model_response run_id=run-123 provider=openai "
        "model=gpt-4.1-2026-01-01 specialist=accessibility_auditor "
        "invocation_id=invocation-123" in caplog.text
    )
    assert "error_type=RuntimeError" in caplog.text
    assert "sensitive upstream response" not in caplog.text
