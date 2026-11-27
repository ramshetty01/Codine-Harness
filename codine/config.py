"""Configuration is runtime state; credentials are never session fields."""
from dataclasses import dataclass, field
import os
import json
from pathlib import Path
from dotenv import load_dotenv

APP_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = "nvidia/nemotron-3.5-super-vl-120b-a12b"
BASE_URL = "https://integrate.api.nvidia.com/v1"


@dataclass
class Config:
    project: Path
    model: str = DEFAULT_MODEL
    api_key: str = ""
    max_tokens: int = 4096
    max_steps: int = 12
    context_chars: int = 160000
    context_tokens: int = 65536
    model_limits: dict = field(default_factory=dict)
    show_reasoning: bool = False
    provider: str = "nvidia"
    budget_usd: str = "3.50"
    request_budget_usd: str = "0.25"
    router_max_price: dict | None = None

    def request_limits(self, model):
        """Local policy ceiling plus explicitly configured model capabilities."""
        limits = self.model_limits.get(model, {})
        window = min(self.context_tokens, limits.get("context_tokens", self.context_tokens))
        output = min(self.max_tokens, limits.get("output_tokens", self.max_tokens))
        if output >= window:
            raise ValueError("Output reservation must be smaller than the context token budget.")
        return window, output

    @classmethod
    def load(cls, project, provider=None, model=None):
        project = Path(project).resolve()
        if not project.is_dir():
            raise ValueError("Project must be an existing directory.")
        load_dotenv(APP_ROOT / ".env")
        if project != APP_ROOT:
            load_dotenv(project / ".env")
        values = {}
        for key, env, default, maximum in (
            ("max_tokens", "CODINE_MAX_TOKENS", 4096, 131072),
            ("max_steps", "CODINE_MAX_STEPS", 12, 100),
            ("context_chars", "CODINE_CONTEXT_CHARS", 160000, 2000000),
            ("context_tokens", "CODINE_CONTEXT_TOKENS", 65536, 10000000),
        ):
            value = int(os.getenv(env, str(default)))
            if not 1 <= value <= maximum:
                raise ValueError(f"{env} must be between 1 and {maximum}.")
            values[key] = value
        limits = json.loads(os.getenv("CODINE_MODEL_LIMITS", "{}"))
        if not isinstance(limits, dict):
            raise ValueError("CODINE_MODEL_LIMITS must be a JSON object keyed by exact model ID.")
        for name, spec in limits.items():
            if not isinstance(name, str) or not name or not isinstance(spec, dict):
                raise ValueError("Invalid model limits entry.")
            if not spec or set(spec) - {"context_tokens", "output_tokens"}:
                raise ValueError("Model limits accept context_tokens and output_tokens only.")
            if any(type(value) is not int or not 1 <= value <= 10000000 for value in spec.values()):
                raise ValueError("Model limits must be positive integer token counts.")
        values["model_limits"] = limits
        provider = provider or os.getenv("CODINE_PROVIDER", "nvidia")
        if provider not in ("nvidia", "openrouter"):
            raise ValueError("CODINE_PROVIDER must be nvidia or openrouter.")
        if provider == "openrouter":
            chosen = model or os.getenv("OPENROUTER_MODEL", "")
        else:
            chosen = model or os.getenv("NVIDIA_MODEL", DEFAULT_MODEL)
        if not chosen:
            raise ValueError("Set OPENROUTER_MODEL explicitly before selecting OpenRouter.")
        return cls(project=project, model=chosen, provider=provider,
                   api_key=os.getenv("OPENROUTER_API_KEY" if provider == "openrouter" else "NVIDIA_API_KEY", ""),
                   budget_usd=os.getenv("CODINE_BUDGET_USD", "3.50"),
                   request_budget_usd=os.getenv("CODINE_REQUEST_BUDGET_USD", "0.25"), **values)


class Redactor:
    """Mask known configured secrets, not arbitrary private information."""
    def __init__(self, secrets=()):
        self.secrets = tuple(sorted({value for value in secrets if value and len(value) > 5}, key=len, reverse=True))

    @classmethod
    def environment(cls):
        return cls(value for key, value in os.environ.items()
                   if any(word in key.upper() for word in ("API_KEY", "TOKEN", "SECRET", "PASSWORD")))

    def text(self, value):
        for secret in self.secrets:
            value = value.replace(secret, "[REDACTED]")
        return value

    def data(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.data(item) for item in value]
        if isinstance(value, dict):
            return {key: self.data(item) for key, item in value.items()}
        return value
