from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Provider = Literal["anthropic", "gemini", "openai", "nvidia"]


class Settings(BaseSettings):
    """Runtime configuration, read from the environment."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    warehouse_url: str = "postgresql://warehouse_ro:warehouse@localhost:5433/semantic"
    app_url: str = "postgresql://app_rw:appsecret@localhost:5433/semantic"
    admin_url: str = "postgresql://postgres:postgres@localhost:5433/semantic"

    anthropic_api_key: str = ""
    google_api_key: str = ""
    openai_api_key: str = ""
    # NVIDIA NIM hosts DeepSeek behind the OpenAI wire protocol, so the key
    # is NVIDIA's (nvapi-...) even though the model is DeepSeek's.
    nvidia_api_key: str = ""

    # Which API answers a question by default. Both sides are held to the
    # same contract -- structured output validated against the same Pydantic
    # models -- so this is a billing decision, not an architectural one.
    llm_provider: Provider = "anthropic"

    # Two tiers per provider. The cheap one answers almost everything in
    # this grammar; the strong one is reserved for questions the asker
    # marks as hard -- see routes/ask.py.
    llm_model: str = "claude-haiku-4-5"
    llm_model_strong: str = "claude-sonnet-5"
    # Both tiers are flash. Every Gemini *pro* model answers a free-tier
    # key with 429 before it reads the question, so routing "hard" there
    # would turn the escalation into a guaranteed failure. 3.7-flash is
    # newer than 3.6 but returned a 503 on two of eight probes, and the
    # tier someone reaches for when a question matters is the wrong place
    # to spend novelty. `make models` re-checks both against your key.
    gemini_model: str = "gemini-3.5-flash"
    gemini_model_strong: str = "gemini-3.6-flash"
    openai_model: str = "gpt-5-mini"
    openai_model_strong: str = "gpt-5"
    nvidia_model: str = "deepseek-ai/deepseek-v3.1"
    nvidia_model_strong: str = "deepseek-ai/deepseek-r1"

    # This was gemini, on the reasoning that a 360-call sweep should not
    # bill. It does not survive contact with the actual quota: Google AI
    # Studio's free tier allows 20 requests per day *per model*, so a full
    # eval there takes about three weeks. Haiku runs the same sweep for
    # roughly thirty cents. Gemini stays a `--provider` away for anyone who
    # has the paid tier.
    eval_provider: Provider = "anthropic"

    layer_dir: Path = Path(__file__).parent / "layer" / "definitions"
    default_ttl_seconds: int = 900
    max_rows: int = 10_000

    def models(self, provider: Provider) -> tuple[str, str]:
        """(default, strong) model ids for a provider.

        anthropic keeps the unprefixed names because it predates the others
        and they are already in people's .env files; renaming them would
        break every existing setup to buy symmetry.
        """
        if provider == "anthropic":
            return self.llm_model, self.llm_model_strong
        return (getattr(self, f"{provider}_model"),
                getattr(self, f"{provider}_model_strong"))


settings = Settings()
