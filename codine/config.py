"""Configuration is runtime state; credentials are never session fields."""
from dataclasses import dataclass
import os
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
    show_reasoning: bool = False

    @classmethod
    def load(cls, project):
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
        ):
            value = int(os.getenv(env, str(default)))
            if not 1 <= value <= maximum:
                raise ValueError(f"{env} must be between 1 and {maximum}.")
            values[key] = value
        return cls(project=project, model=os.getenv("NVIDIA_MODEL", DEFAULT_MODEL),
                   api_key=os.getenv("NVIDIA_API_KEY", ""), **values)


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
