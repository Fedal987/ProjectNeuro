"""Application settings. Importing this module never reads configuration files."""
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from src.main.encoding import read_text_file
from src.main.model_config import ModelOptions, parse_model_options

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


@dataclass(frozen=True)
class APIConfig:
    base_url: str
    api_key: str = field(repr=False)
    model: str
    stream: bool = True
    temperature: float = 0.2
    protocol: str = "openai_compatible"
    provider: str = "auto"
    defaults: ModelOptions = field(default_factory=ModelOptions)
    models: dict[str, ModelOptions] = field(default_factory=dict)

    def __post_init__(self):
        for name in ("base_url", "api_key", "model"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"API_MANAGER.{name.upper()} must be a non-empty string")
        url = urlsplit(self.base_url)
        if url.scheme not in {"http", "https"} or not url.netloc:
            raise ValueError("API_MANAGER.BASE_URL must be an HTTP(S) URL")
        if type(self.stream) is not bool:
            raise ValueError("API_MANAGER.STREAM must be a boolean")
        if type(self.temperature) not in (int, float) or not isfinite(self.temperature) or self.temperature < 0:
            raise ValueError("API_MANAGER.TEMPERATURE must be a finite non-negative number")
        for name in ("protocol", "provider"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError(f"API_MANAGER.{name.upper()} must be a non-empty identifier")
        if not isinstance(self.defaults, ModelOptions):
            raise ValueError("API_MANAGER.DEFAULTS must contain model options")
        if not isinstance(self.models, dict) or any(
            not isinstance(name, str) or not name.strip() or any(char.isspace() for char in name)
            or not isinstance(options, ModelOptions)
            for name, options in self.models.items()
        ):
            raise ValueError("API_MANAGER.MODELS must map model identifiers to model options")


@dataclass(frozen=True)
class ReasoningConfig:
    enabled: bool = True
    thinking: bool = True
    max_steps: int = 12
    effort: str = ""
    auto_approve: bool = False
    command_timeout: int = 60

    def __post_init__(self):
        for name in ("enabled", "thinking", "auto_approve"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"REASONING.{name.upper()} must be a boolean")
        for name in ("max_steps", "command_timeout"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"REASONING.{name.upper()} must be a positive integer")
        if not isinstance(self.effort, str):
            raise ValueError("REASONING.EFFORT must be a string")


@dataclass(frozen=True)
class AppConfig:
    api: APIConfig
    reasoning: ReasoningConfig = field(default_factory=ReasoningConfig)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "AppConfig":
        api = data.get("API_MANAGER")
        reasoning = data.get("REASONING", {})
        if not isinstance(api, dict) or not isinstance(reasoning, dict):
            raise ValueError("Configuration requires an API_MANAGER table and an optional REASONING table")
        for key in ("BASE_URL", "API_KEY", "MODEL"):
            if key not in api:
                raise ValueError(f"Missing API_MANAGER.{key}")
        return cls(
            api=APIConfig(
                base_url=api["BASE_URL"], api_key=api["API_KEY"], model=api["MODEL"],
                stream=api.get("STREAM", True),
                # Preserve the spelling used by existing config.toml files.
                temperature=api.get("TEMPERATURE", api.get("TEMPREATURE", 0.2)),
                protocol=api.get("PROTOCOL", "openai_compatible"),
                provider=api.get("PROVIDER", "auto"),
                defaults=ModelOptions.from_mapping(api.get("DEFAULTS", {})),
                models=parse_model_options(api.get("MODELS", {})),
            ),
            reasoning=ReasoningConfig(
                enabled=reasoning.get("ENABLED", True), thinking=reasoning.get("THINKING", True),
                max_steps=reasoning.get("MAX_STEPS", 12), effort=reasoning.get("EFFORT", ""),
                auto_approve=reasoning.get("AUTO_APPROVE", False),
                command_timeout=reasoning.get("COMMAND_TIMEOUT", 60),
            ),
        )


def load_config(path: str | Path | None = None) -> AppConfig:
    """Read and validate at startup; an explicit path never falls back elsewhere."""
    if path is None:
        path = Path(__file__).resolve().parents[2] / "config.toml"
        if not path.is_file():
            path = Path.cwd() / "config.toml"
    path = Path(path).expanduser()
    data = tomllib.loads(read_text_file(path)[0])
    return AppConfig.from_mapping(data)
