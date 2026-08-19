"""Small operational commands.

`models` answers the one question a provider switch actually raises: which
model ids will this API accept today? Vendors rename and retire tiers, and
a stale id in .env surfaces as a 404 mid-question, which is the worst place
to find out.

Usage:  python -m app.cli models [--provider gemini]
"""

from __future__ import annotations

import argparse

from .config import settings
from .llm.client import LLMError, configured_providers


def _anthropic_models() -> list[str]:
    import anthropic

    return [m.id for m in anthropic.Anthropic().models.list(limit=50)]


def _gemini_models() -> list[str]:
    from google import genai

    client = genai.Client(api_key=settings.google_api_key or None)
    return sorted(
        m.name.removeprefix("models/")
        for m in client.models.list()
        if "generateContent" in (m.supported_actions or [])
    )


def _openai_models() -> list[str]:
    import openai

    return sorted(m.id for m in openai.OpenAI(
        api_key=settings.openai_api_key or None).models.list())


def _nvidia_models() -> list[str]:
    import openai

    return sorted(m.id for m in openai.OpenAI(
        api_key=settings.nvidia_api_key or None,
        base_url="https://integrate.api.nvidia.com/v1").models.list())


LISTERS = {"anthropic": _anthropic_models, "gemini": _gemini_models,
           "openai": _openai_models, "nvidia": _nvidia_models}


def _same(listed: str, configured: str) -> bool:
    """An alias resolves to a dated id and is not itself listed, so a bare
    prefix would be the obvious test -- and the wrong one: it makes
    `gemini-2.5-flash` claim `gemini-2.5-flash-lite` as itself. Only a date
    suffix counts as the same model."""
    return listed == configured or listed.startswith(configured + "-20")


def models(providers: list[str]) -> int:
    for provider in providers:
        cheap, strong = settings.models(provider)
        print(f"\n== {provider}  (default: {cheap}, hard: {strong})")
        try:
            available = LISTERS[provider]()
        except Exception as exc:                    # noqa: BLE001
            print(f"   could not list models: {exc}")
            continue
        # NVIDIA lists hundreds of hosted models; only the family in play
        # is useful to read.
        if provider == "nvidia":
            available = [m for m in available if "deepseek" in m.lower()] or available
        for m in available:
            mark = (" <- default" if _same(m, cheap)
                    else " <- hard" if _same(m, strong) else "")
            print(f"   {m}{mark}")
        for configured, role in ((cheap, "default"), (strong, "hard")):
            if not any(_same(m, configured) for m in available):
                print(f"   !! the {role} model {configured!r} matches nothing "
                      f"in that list; fix it in .env before it 404s "
                      f"mid-question")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="app.cli")
    sub = ap.add_subparsers(dest="command", required=True)
    m = sub.add_parser("models", help="list model ids each API accepts")
    m.add_argument("--provider", choices=sorted(LISTERS), default=None,
                   help="default: every provider with a key configured")

    args = ap.parse_args()
    if args.command == "models":
        chosen = [args.provider] if args.provider else configured_providers()
        if not chosen:
            raise LLMError("No API credential is configured. Put "
                           "ANTHROPIC_API_KEY or GOOGLE_API_KEY in .env.")
        return models(chosen)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
