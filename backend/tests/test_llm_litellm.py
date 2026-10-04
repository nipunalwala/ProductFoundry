"""The LiteLLM adapter, with `litellm.completion` replaced. No network."""

from types import SimpleNamespace

import pytest

from productfoundry.llm import ProviderError, ProviderRequest, live_providers, load_routing
from productfoundry.settings import Settings

REQUEST = ProviderRequest("review_sentiment", "groq/openai/gpt-oss-120b", [], "hash", 30.0)


@pytest.fixture
def litellm_module(monkeypatch):
    import litellm

    return litellm


def provider():
    from productfoundry.llm.litellm_provider import LiteLLMProvider

    return LiteLLMProvider("test-key")


def test_a_response_becomes_text_and_token_counts(litellm_module, monkeypatch):
    seen = {}

    def completion(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=3),
        )

    monkeypatch.setattr(litellm_module, "completion", completion)
    response = provider().complete(REQUEST)
    assert (response.text, response.input_tokens, response.output_tokens) == ('{"ok": true}', 12, 3)
    assert seen["model"] == REQUEST.model
    assert seen["num_retries"] == 0
    assert seen["timeout"] == 30.0
    assert seen["response_format"] == {"type": "json_object"}


def raising(litellm_module, name, **extra):
    errors = litellm_module.exceptions
    kwargs = {"message": "Bearer sk-secret was refused", "llm_provider": "groq", "model": "m"}
    return getattr(errors, name)(**kwargs, **extra)


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("RateLimitError", "rate_limit"),
        ("Timeout", "timeout"),
        ("InternalServerError", "server_error"),
        ("ServiceUnavailableError", "server_error"),
        ("AuthenticationError", "rejected"),
        ("BadRequestError", "rejected"),
    ],
)
def test_provider_errors_are_classified_and_never_quote_the_provider(
    litellm_module, monkeypatch, name, kind
):
    def completion(**kwargs):
        raise raising(litellm_module, name)

    monkeypatch.setattr(litellm_module, "completion", completion)
    with pytest.raises(ProviderError) as error:
        provider().complete(REQUEST)
    assert error.value.kind == kind
    assert "sk-secret" not in str(error.value)
    assert error.value.__cause__ is None


def test_live_providers_exist_only_for_keys_that_are_set():
    routing = load_routing()
    settings = Settings(_env_file=None, groq_api_key="test-key", gemini_api_key="")
    assert list(live_providers(routing, settings)) == ["groq"]
    assert "test-key" not in repr(settings)
