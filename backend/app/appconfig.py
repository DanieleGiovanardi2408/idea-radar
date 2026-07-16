"""Loader della configurazione comportamentale letta da ``config.yaml``.

Distinto da ``app.config`` (che legge i *segreti* dall'ambiente/.env):
qui vive il *comportamento* della pipeline — fonti, keyword, scoring.
"""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


class SourceConfig(BaseModel):
    name: str
    type: str  # "hn" | "github" | "rss"
    limit: int = 30
    enabled: bool = True
    # Solo per type: rss — elenco di feed (riviste, blog, forum).
    feeds: list[str] = Field(default_factory=list)


class ClusteringConfig(BaseModel):
    # Similarità coseno minima per fondere due item nella stessa idea.
    idea_threshold: float = 0.82
    # Similarità minima (più permissiva) per mettere due idee nello stesso topic.
    topic_threshold: float = 0.62
    # Se True, il topic viene nominato dall'LLM invece che ereditare l'etichetta.
    llm_topic_labels: bool = True


class ScoringConfig(BaseModel):
    weights: dict[str, float] = Field(default_factory=dict)
    threshold: float = 0.6
    # Quanto "sopravvive" un'idea del tutto fuori tema (fit=0):
    # composite = quality * (relevance_floor + (1 - relevance_floor) * fit).
    relevance_floor: float = 0.25
    # Fit minimo perché a un item valga la pena spendere l'insight LLM: sotto
    # questa soglia si usa l'insight euristico (niente 7B). 0.0 = salta solo i
    # fit == 0, cioè gli item senza NESSUN match di keyword.
    insight_min_fit: float = 0.0

    @field_validator("weights")
    @classmethod
    def _non_empty(cls, v: dict[str, float]) -> dict[str, float]:
        if not v:
            raise ValueError("scoring.weights non può essere vuoto")
        return v

    def normalized_weights(self) -> dict[str, float]:
        """Pesi riscalati in modo che sommino a 1 (robusto a config approssimative)."""
        total = sum(self.weights.values())
        if total <= 0:
            raise ValueError("La somma dei pesi deve essere > 0")
        return {k: w / total for k, w in self.weights.items()}


class AppConfig(BaseModel):
    sources: list[SourceConfig] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    scoring: ScoringConfig
    clustering: ClusteringConfig = Field(default_factory=ClusteringConfig)

    def enabled_sources(self) -> list[SourceConfig]:
        return [s for s in self.sources if s.enabled]


def load_config(path: Path | None = None) -> AppConfig:
    """Carica e valida ``config.yaml``."""
    cfg_path = path or CONFIG_PATH
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(data)


@lru_cache
def get_config() -> AppConfig:
    return load_config()
