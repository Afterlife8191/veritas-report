"""Provider-agnostic LLM access."""

from .base import Completion, LLMProvider, ProviderError
from .mock import MockProvider, ScriptedProvider

__all__ = [
    "Completion",
    "LLMProvider",
    "MockProvider",
    "ProviderError",
    "ScriptedProvider",
    "get_provider",
]


def get_provider(name: str, model: str | None = None) -> LLMProvider:
    """Resolve a provider by name.

    The Anthropic client is imported lazily so that the mock path never needs the
    optional dependency installed.
    """
    if name == "mock":
        return MockProvider()
    if name == "anthropic":
        from .anthropic_client import AnthropicProvider

        return AnthropicProvider(model=model)
    raise ValueError(f"unknown provider: {name!r} (expected 'mock' or 'anthropic')")
