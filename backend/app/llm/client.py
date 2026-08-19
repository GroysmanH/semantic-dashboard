"""The model seam.

Deliberately narrow: one method, structured output only, no free-form text
path. Swapping in a different model is a constructor argument, which is
what makes the eval's by-model comparison a measurement rather than a
rewrite.
"""

from __future__ import annotations

from typing import Protocol, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from ..config import settings

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = "claude-haiku-4-5"


class LLMSchemaError(RuntimeError):
    """The model answered, but outside the grammar -- three dimensions where
    two are allowed, say. Structured output constrains the JSON shape, not
    every bound in the schema, so this is a normal outcome rather than an
    exceptional one, and it is worth one retry with the reason attached."""


class LLMError(RuntimeError):
    """Carries a sentence a manager can act on. The underlying exception is
    still chained for the logs -- a raw provider payload in the card is
    noise to the person reading it."""


def _schema_reason(exc: ValidationError) -> str:
    """One sentence per violated bound, in the grammar's own terms."""
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"] if not isinstance(p, int))
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts) or "the answer did not fit the query grammar"


def _explain(status: int, model: str) -> str:
    if status in (401, 403):
        return ("The model credential was rejected. Set a valid "
                "ANTHROPIC_API_KEY and restart.")
    if status == 404:
        return f"The model {model!r} is not available on this account."
    if status == 429:
        return "The model is rate limited right now. Try again in a moment."
    if status >= 500:
        return "The model service is unavailable. Try again shortly."
    return f"The model rejected the request (HTTP {status})."


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
        except ValidationError as exc:
            raise LLMSchemaError(_schema_reason(exc)) from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(_explain(exc.status_code, self.model)) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("The model service could not be reached. "
                           "Check the network and try again.") from exc

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
