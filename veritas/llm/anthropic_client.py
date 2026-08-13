"""Anthropic-backed provider.

Optional: the demo and the tests never reach this file. It is imported lazily so
the project keeps working with no third-party packages installed and no API key
set. Install the extra with ``pip install -e '.[anthropic]'`` to use it.

The key is read from the environment and never written to disk, logs, or the
audit trail; only the model id is recorded.
"""

from __future__ import annotations

import os

from .base import Completion, LLMProvider, ProviderError

DEFAULT_MODEL = "claude-opus-5"
API_KEY_ENV = "ANTHROPIC_API_KEY"
MODEL_ENV = "VERITAS_MODEL"
DEFAULT_MAX_TOKENS = 8000


class AnthropicProvider(LLMProvider):
    """Calls the Anthropic Messages API."""

    name = "anthropic"

    def __init__(self, model: str | None = None, max_tokens: int = DEFAULT_MAX_TOKENS) -> None:
        self.model = model or os.environ.get(MODEL_ENV) or DEFAULT_MODEL
        self.max_tokens = max_tokens
        self._client = None

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        if not os.environ.get(API_KEY_ENV):
            raise ProviderError(
                f"{API_KEY_ENV} is not set. Run with --provider mock to use the "
                "deterministic writer instead."
            )
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise ProviderError(
                "the 'anthropic' package is not installed; "
                "install it with: pip install -e '.[anthropic]'"
            ) from exc
        self._client = anthropic.Anthropic()
        return self._client

    def complete(self, system: str, user: str) -> Completion:
        client = self._ensure_client()
        response = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        if response.stop_reason == "refusal":
            raise ProviderError("the model declined the request")

        text = "".join(block.text for block in response.content if block.type == "text")
        if not text.strip():
            raise ProviderError(f"empty response (stop_reason={response.stop_reason})")

        return Completion(
            text=text,
            provider=self.name,
            model=response.model,
            metadata={
                "stop_reason": response.stop_reason,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        )
