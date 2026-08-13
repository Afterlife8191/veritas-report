"""The provider interface the writing layer speaks to.

Two implementations ship with the project: a deterministic mock (used by the
demo and the whole test suite, so neither needs an API key) and a real Anthropic
client. Everything above this boundary is provider-agnostic, and everything below
it is untrusted -- whatever comes back is text until the validator says otherwise.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Completion:
    """One model response."""

    text: str
    provider: str
    model: str
    metadata: dict = field(default_factory=dict)


class LLMProvider(ABC):
    """Minimal surface: one prompt in, one string out."""

    #: Stable identifier recorded in the audit trail.
    name: str = "provider"
    model: str = "unknown"

    @abstractmethod
    def complete(self, system: str, user: str) -> Completion:
        """Return the model's response to ``system`` + ``user``."""


class ProviderError(RuntimeError):
    """The provider could not produce a response. Never retried silently."""
