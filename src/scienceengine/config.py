import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelConfig:
    model: str
    base_url: str
    api_key: str = field(repr=False)

    def __post_init__(self):
        if not all((self.model.strip(), self.base_url.strip(), self.api_key.strip())):
            raise ValueError("model, base_url and api_key must all be configured")

    @classmethod
    def from_env(cls):
        """No implicit model selection; the same config can serve both extraction chains."""
        return cls(os.environ["LLM_MODEL"], os.environ["LLM_BASE_URL"], os.environ["LLM_API_KEY"])

    def public(self):
        return {"model": self.model, "base_url": self.base_url.rstrip("/")}
