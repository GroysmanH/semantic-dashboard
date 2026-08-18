"""The model seam.

Deliberately narrow: one method, structured output only, no free-form text
path. Swapping in a different model is a constructor argument, which is
what makes the eval's by-model comparison a measurement rather than a
rewrite.
"""

from __future__ import annotations

from typing import Protocol, TypeVar

import anthropic
from pydantic import BaseModel

from ..config import settings

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = "claude-sonnet-5"


class LLMError(RuntimeError):
    pass


class LLMClient(Protocol):
    def ask(self, system: str, user: str, schema: type[T]) -> T: ...


class AnthropicClient:
    """Structured output via messages.parse: the schema is enforced
    server-side, so a malformed response never reaches the compiler."""

    def __init__(self, model: str | None = None, max_tokens: int = 4096) -> None:
        self.model = model or settings.llm_model or DEFAULT_MODEL
        self.max_tokens = max_tokens
        self.client = anthropic.Anthropic()   # reads ANTHROPIC_API_KEY
        self.last_usage: dict[str, int] = {}

    def ask(self, system: str, user: str, schema: type[T]) -> T:
        try:
            response = self.client.messages.parse(
                model=self.model,
                max_tokens=self.max_tokens,
                # The layer prompt is large and byte-identical across
                # requests, so it sits behind a cache breakpoint and the
                # volatile question goes after it.
                system=[{"type": "text", "text": system,
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_format=schema,
            )
        except anthropic.APIStatusError as exc:
            raise LLMError(f"{exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("could not reach the model") from exc

        usage = response.usage
        self.last_usage = {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "cache_creation_input_tokens":
                getattr(usage, "cache_creation_input_tokens", 0) or 0,
        }

        if response.parsed_output is None:
            raise LLMError(f"model returned no structured output "
                           f"(stop_reason={response.stop_reason})")
        return response.parsed_output
