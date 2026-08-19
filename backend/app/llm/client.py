"""The model seam.

Deliberately narrow: one method, structured output only, no free-form text
path. Swapping in a different model -- or a different vendor -- is a
constructor argument, which is what makes the eval's by-model comparison a
measurement rather than a rewrite.

Two providers live here, Anthropic and Google AI Studio. They are held to
the same contract on purpose: both are handed the same system prompt and
the same Pydantic model, and both are validated client-side against that
model afterwards. A response that invents a field or exceeds a declared
bound is an `LLMSchemaError` on either side, so an eval run comparing them
is comparing the models and not two different amounts of leniency.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from ..config import Provider, settings

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"

# Reasoning models on OpenAI-compatible endpoints often narrate before they
# answer, and some return that narration inside the content rather than in a
# separate field. The JSON is what we want; the thinking is not ours to read.
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _extract_json(text: str) -> str:
    """The JSON object in a response that may be wrapped in narration.

    A strict endpoint returns bare JSON and this is a no-op. A lenient one
    may prepend a <think> block or wrap the answer in a code fence, and
    handing that to Pydantic would report a grammar failure for what is
    really a formatting one -- then burn a retry on it.
    """
    text = _FENCE.sub("", _THINK.sub("", text)).strip()
    if text.startswith("{"):
        return text
    start, depth, in_str, esc = text.find("{"), 0, False, False
    if start < 0:
        return text
    for i in range(start, len(text)):
        ch = text[i]
        if esc:
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == '"':
            in_str = not in_str
        elif not in_str:
            depth += 1 if ch == "{" else -1 if ch == "}" else 0
            if depth == 0:
                return text[start:i + 1]
    return text[start:]


class LLMSchemaError(RuntimeError):
    """The model answered, but outside the grammar -- three dimensions where
    two are allowed, say. Structured output constrains the JSON shape, not
    every bound in the schema, so this is a normal outcome rather than an
    exceptional one, and it is worth one retry with the reason attached."""


class LLMError(RuntimeError):
    """Carries a sentence a manager can act on. The underlying exception is
    still chained for the logs -- a raw provider payload in the card is
    noise to the person reading it."""


class LLMRateLimited(LLMError):
    """Terminal for a single request, but not for a batch.

    A card refuses and says to try again; the eval, which is deliberately
    pointed at a free tier and will meet this constantly, waits instead.
    Same condition, two right answers, so it needs its own type.
    """


def _schema_reason(exc: ValidationError) -> str:
    """One sentence per violated bound, in the grammar's own terms."""
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"] if not isinstance(p, int))
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts) or "the answer did not fit the query grammar"


def _error_for(status: int, model: str, key_var: str) -> LLMError:
    cls = LLMRateLimited if status == 429 else LLMError
    return cls(_explain(status, model, key_var))


def _explain(status: int, model: str, key_var: str) -> str:
    if status in (401, 403):
        return (f"The model credential was rejected. Set a valid "
                f"{key_var} and restart.")
    if status == 404:
        return f"The model {model!r} is not available on this account."
    if status == 429:
        return "The model is rate limited right now. Try again in a moment."
    if status >= 500:
        return "The model service is unavailable. Try again shortly."
    return f"The model rejected the request (HTTP {status})."


class LLMClient(Protocol):
    provider: Provider
    model: str

    def ask(self, system: str, user: str, schema: type[T]) -> T: ...


class AnthropicClient:
    """Structured output via messages.parse: the schema is enforced
    server-side, so a malformed response never reaches the compiler."""

    provider: Provider = "anthropic"
    key_var = "ANTHROPIC_API_KEY"

    def __init__(self, model: str | None = None, max_tokens: int = 4096) -> None:
        self.model = model or settings.llm_model or DEFAULT_MODEL
        self.max_tokens = max_tokens
        self.last_usage: dict[str, int] = {}
        self._sdk = None

    def _client(self):
        # Built on first use, not in __init__: a missing credential then
        # arrives as an LLMError the card can show, rather than a traceback
        # at import time in a process that may never call the model.
        if self._sdk is None:
            import anthropic
            try:
                self._sdk = anthropic.Anthropic()   # reads ANTHROPIC_API_KEY
            except Exception as exc:                # noqa: BLE001
                raise LLMError("No Anthropic credential is configured. Put "
                               "ANTHROPIC_API_KEY in .env and restart.") from exc
        return self._sdk

    def ask(self, system: str, user: str, schema: type[T]) -> T:
        import anthropic

        try:
            response = self._client().messages.parse(
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
            raise _error_for(exc.status_code, self.model, self.key_var) from exc
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


class GeminiClient:
    """Google AI Studio, same contract.

    The grammar goes over as `response_json_schema` -- Pydantic's own JSON
    Schema, verbatim. Not `response_schema`: that one is translated into
    the SDK's narrower Schema type, which carries `additional_properties`
    locally but has nowhere to put it on the wire, so `extra="forbid"`
    turns into a 400 rather than a constraint. The JSON Schema path takes
    `$defs`, `$ref`, `anyOf` and `additionalProperties` as written.

    The response is then re-validated here against the original model
    rather than trusting the SDK's own `.parsed`, so every declared bound
    stays load-bearing and a schema miss lands on the same retryable
    footing it has on the Anthropic path.

    Gemini's thinking tokens are drawn from the same output budget as the
    answer, so the budget here is larger than Anthropic's; a truncated
    answer would otherwise arrive as unparseable JSON with no explanation.
    """

    provider: Provider = "gemini"
    key_var = "GOOGLE_API_KEY"

    def __init__(self, model: str | None = None, max_tokens: int = 8192) -> None:
        self.model = model or settings.gemini_model or DEFAULT_GEMINI_MODEL
        self.max_tokens = max_tokens
        self.last_usage: dict[str, int] = {}
        self._sdk = None

    def _client(self):
        if self._sdk is None:
            from google import genai
            try:
                # Falls back to GEMINI_API_KEY/GOOGLE_API_KEY in the
                # environment when the setting is unset.
                self._sdk = genai.Client(api_key=settings.google_api_key or None)
            except Exception as exc:                # noqa: BLE001
                raise LLMError("No Google AI Studio credential is configured. "
                               "Put GOOGLE_API_KEY in .env and restart.") from exc
        return self._sdk

    def ask(self, system: str, user: str, schema: type[T]) -> T:
        from google.genai import errors, types

        try:
            response = self._client().models.generate_content(
                model=self.model,
                contents=user,
                config=types.GenerateContentConfig(
                    # No cache_control equivalent: 2.5-series models cache
                    # a repeated prefix implicitly, and the layer prompt is
                    # byte-stable, so the same prefix is what gets reused.
                    system_instruction=system,
                    response_mime_type="application/json",
                    response_json_schema=schema.model_json_schema(),
                    max_output_tokens=self.max_tokens,
                    # No tools are ever declared here, and leaving the
                    # feature on makes the SDK warn on every single call.
                    automatic_function_calling=types.
                    AutomaticFunctionCallingConfig(disable=True),
                ),
            )
        except errors.APIError as exc:
            raise _error_for(exc.code or 500, self.model, self.key_var) from exc
        except Exception as exc:                    # noqa: BLE001
            raise LLMError("The model service could not be reached. "
                           "Check the network and try again.") from exc

        usage = getattr(response, "usage_metadata", None)
        self.last_usage = {
            "input_tokens": getattr(usage, "prompt_token_count", 0) or 0,
            "output_tokens": getattr(usage, "candidates_token_count", 0) or 0,
            "cache_read_input_tokens":
                getattr(usage, "cached_content_token_count", 0) or 0,
            "cache_creation_input_tokens": 0,
        }

        text = (response.text or "").strip()
        if not text:
            finish = None
            if response.candidates:
                finish = getattr(response.candidates[0], "finish_reason", None)
            raise LLMError(f"model returned no structured output "
                           f"(finish_reason={finish})")

        try:
            return schema.model_validate_json(text)
        except ValidationError as exc:
            raise LLMSchemaError(_schema_reason(exc)) from exc


class _OpenAICompatible:
    """Any endpoint speaking the OpenAI chat protocol.

    Two live here because two exist: OpenAI itself, and NVIDIA NIM, which
    hosts DeepSeek behind the same wire format. The difference between them
    is a base URL, a key, and how much structure the server will enforce --
    which is what `structured` selects:

      parse        the SDK's strict schema mode; the server rejects a
                   response that does not fit
      json_object  the server only promises valid JSON, and the schema
                   travels in the prompt

    Either way the answer is re-validated here against the original model,
    so `extra="forbid"` and every declared bound stay load-bearing and a
    miss lands on the same retryable footing it has on the other providers.
    That matters for more than tidiness: it is what keeps an eval across
    four vendors a comparison of models rather than of four different
    amounts of leniency.
    """

    provider: Provider
    key_var: str
    base_url: str | None = None
    structured: str = "parse"
    default_model: str = ""

    def __init__(self, model: str | None = None, max_tokens: int = 4096) -> None:
        self.model = model or self.default_model
        self.max_tokens = max_tokens
        self.last_usage: dict[str, int] = {}
        self._sdk = None

    def _api_key(self) -> str:
        return getattr(settings, self.key_var.lower(), "")

    def _client(self):
        if self._sdk is None:
            import openai
            try:
                self._sdk = openai.OpenAI(api_key=self._api_key() or None,
                                          base_url=self.base_url)
            except Exception as exc:                # noqa: BLE001
                raise LLMError(f"No {self.provider} credential is configured. "
                               f"Put {self.key_var} in .env and restart.") from exc
        return self._sdk

    def _messages(self, system: str, user: str, schema: type[T]) -> list[dict]:
        if self.structured == "parse":
            return [{"role": "system", "content": system},
                    {"role": "user", "content": user}]
        # Without server-side enforcement the schema has to travel in the
        # prompt. Worth naming: this provider is being asked a slightly
        # different question from the others, and the eval should read its
        # numbers with that in mind.
        return [
            {"role": "system", "content":
                f"{system}\n\nReply with a single JSON object and nothing "
                f"else -- no prose, no code fence. It must match this JSON "
                f"Schema exactly, with no additional properties:\n"
                f"{json.dumps(schema.model_json_schema(), sort_keys=True)}"},
            {"role": "user", "content": user},
        ]

    def ask(self, system: str, user: str, schema: type[T]) -> T:
        import openai

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self._messages(system, user, schema),
            "max_completion_tokens": self.max_tokens,
        }
        try:
            if self.structured == "parse":
                response = self._client().chat.completions.parse(
                    response_format=schema, **kwargs)
            else:
                response = self._client().chat.completions.create(
                    response_format={"type": "json_object"}, **kwargs)
        except openai.LengthFinishReasonError as exc:
            raise LLMError("The answer was cut off before it was complete. "
                           "Try a shorter question.") from exc
        except openai.APIStatusError as exc:
            raise _error_for(exc.status_code, self.model, self.key_var) from exc
        except openai.APIConnectionError as exc:
            raise LLMError("The model service could not be reached. "
                           "Check the network and try again.") from exc

        usage = response.usage
        cached = getattr(getattr(usage, "prompt_tokens_details", None),
                         "cached_tokens", 0) or 0
        self.last_usage = {
            "input_tokens": (usage.prompt_tokens if usage else 0) or 0,
            "output_tokens": (usage.completion_tokens if usage else 0) or 0,
            "cache_read_input_tokens": cached,
            "cache_creation_input_tokens": 0,
        }

        message = response.choices[0].message
        parsed = getattr(message, "parsed", None)
        if parsed is not None:
            return parsed

        text = _extract_json(message.content or "")
        if not text:
            raise LLMError(f"model returned no structured output "
                           f"(finish_reason={response.choices[0].finish_reason})")
        try:
            return schema.model_validate_json(text)
        except ValidationError as exc:
            raise LLMSchemaError(_schema_reason(exc)) from exc


class OpenAIClient(_OpenAICompatible):
    provider: Provider = "openai"
    key_var = "OPENAI_API_KEY"
    structured = "parse"

    @property
    def default_model(self) -> str:
        return settings.openai_model


class NvidiaClient(_OpenAICompatible):
    """DeepSeek and friends, hosted by NVIDIA behind the OpenAI protocol.

    `json_object` rather than strict parsing: NIM's schema enforcement
    varies by model, and a provider that 400s on an unsupported keyword is
    worse than one that returns JSON we check ourselves.
    """

    provider: Provider = "nvidia"
    key_var = "NVIDIA_API_KEY"
    base_url = "https://integrate.api.nvidia.com/v1"
    structured = "json_object"

    @property
    def default_model(self) -> str:
        return settings.nvidia_model


CLIENTS: dict[Provider, type] = {
    "anthropic": AnthropicClient,
    "gemini": GeminiClient,
    "openai": OpenAIClient,
    "nvidia": NvidiaClient,
}


def make_client(provider: Provider | None = None, *, hard: bool = False,
                model: str | None = None) -> LLMClient:
    """Pick the provider and, within it, the tier.

    `hard` is the only escalator, and it escalates within whichever
    provider is in play -- so switching to Gemini does not quietly re-price
    what "hard" costs.
    """
    provider = provider or settings.llm_provider
    if provider not in CLIENTS:
        raise LLMError(f"Unknown model provider {provider!r}. "
                       f"Expected one of: {', '.join(CLIENTS)}.")
    if model is None:
        cheap, strong = settings.models(provider)
        model = strong if hard else cheap
    return CLIENTS[provider](model)


def configured_providers() -> list[Provider]:
    """Providers with a credential present.

    The card only offers what can actually answer: a selector listing an
    API whose key is missing turns a configuration mistake into a failed
    question, which is a worse place to discover it.
    """
    import os

    present = {
        "anthropic": settings.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY"),
        "gemini": (settings.google_api_key or os.getenv("GOOGLE_API_KEY")
                   or os.getenv("GEMINI_API_KEY")),
        "openai": settings.openai_api_key or os.getenv("OPENAI_API_KEY"),
        "nvidia": settings.nvidia_api_key or os.getenv("NVIDIA_API_KEY"),
    }
    return [p for p in CLIENTS if present.get(p)]
