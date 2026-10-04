"""Gateway, routing table, quota, cache and provenance.

Stages name a task and never a provider or model. LiteLLM is imported only by
`llm.litellm_provider`, which is loaded on demand by `live_providers`.
"""

from productfoundry.llm.gateway import Gateway
from productfoundry.llm.routing import Routing, load_routing
from productfoundry.llm.types import (
    CallStore,
    LlmCall,
    LlmFailed,
    Message,
    Provider,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    UsageStore,
)
from productfoundry.settings import Settings

__all__ = [
    "CallStore",
    "Gateway",
    "LlmCall",
    "LlmFailed",
    "Message",
    "Provider",
    "ProviderError",
    "ProviderRequest",
    "ProviderResponse",
    "Routing",
    "UsageStore",
    "live_providers",
    "load_routing",
]


def live_providers(routing: Routing, settings: Settings) -> dict[str, Provider]:
    """A real provider for every routing entry whose API key is set."""
    from productfoundry.llm.litellm_provider import LiteLLMProvider

    providers: dict[str, Provider] = {}
    for name, spec in routing.providers.items():
        key = getattr(settings, spec.api_key_setting, None)
        if key is not None and key.get_secret_value():
            providers[name] = LiteLLMProvider(key.get_secret_value())
    return providers
