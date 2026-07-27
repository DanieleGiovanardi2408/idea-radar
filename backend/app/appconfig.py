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
    type: str  # "hn" | "hn_algolia" | "github" | "rss"
    limit: int = 30
    enabled: bool = True
    # Solo per type: rss — elenco di feed (riviste, blog, forum).
    feeds: list[str] = Field(default_factory=list)
    # Solo per type: hn_algolia — finestra di backfill (ore guardate indietro
    # a ogni run) e punti minimi perché una storia non sia rumore.
    lookback_hours: float = 48.0
    min_points: int = 5
    # Solo per type: arxiv — categorie arXiv in OR (es. ["cs.AI", "cs.SE"]);
    # se vuote si ripiega sulle keywords globali di config.
    categories: list[str] = Field(default_factory=list)
    # Solo per type: github — fasce di ETÀ dei repo cercati, in giorni. Ogni
    # valore è il bordo superiore di una fascia: [90, 270, 540] cerca in 0-90,
    # 90-270 e 270-540 giorni. Senza vincolo sulla nascita la Search API
    # ordinata per stelle restituisce i più stellati di sempre, cioè mercati
    # chiusi; una fascia sola invece si esaurisce in fretta (la stessa query
    # ridà gli stessi repo a ogni run), mentre la fascia più giovane si rinnova
    # da sé man mano che nascono progetti.
    created_windows: list[int] = Field(default_factory=lambda: [90, 270, 540])
    min_stars: int = 10
    # Solo per type: huggingface — cosa cercare sull'hub ("models", "datasets").
    hf_kinds: list[str] = Field(default_factory=lambda: ["models", "datasets"])
    # Solo per type: stackexchange — tag da seguire e sito della rete.
    tags: list[str] = Field(default_factory=list)
    site: str = "stackoverflow"
    # Solo per type: stackexchange/npm — età massima (giorni) di ciò che conta
    # come "nuovo". Oltre, non è più un segnale emergente.
    max_age_days: int = 60


class ClusteringConfig(BaseModel):
    # Similarità coseno minima perché un item entri nell'idea di un item già
    # visto (legame singolo). Default allineati a config.yaml: sono tarati sui
    # dati reali, non scelti a occhio.
    idea_threshold: float = 0.86
    # Coesione: similarità minima verso OGNI membro dell'idea. Impedisce la
    # catena A~B~C con A e C estranei. 0.0 = criterio disattivato.
    cohesion_floor: float = 0.82
    # Similarità minima (più permissiva) per mettere due idee nello stesso topic.
    topic_threshold: float = 0.78
    # Se True, il topic viene nominato dall'LLM invece che ereditare l'etichetta.
    llm_topic_labels: bool = True
    # Idee minime perché un topic valga una chiamata al modello per il nome.
    # Sotto, eredita il titolo dell'idea che lo apre. E comunque si rinominano
    # solo i topic la cui composizione è cambiata: quelli fermi no.
    topic_label_min_ideas: int = 3


class ScoringConfig(BaseModel):
    weights: dict[str, float] = Field(default_factory=dict)
    threshold: float = 0.32
    # Quanto "sopravvive" un'idea del tutto fuori tema (fit=0) o già satura
    # (opportunity=0). Sono due moltiplicatori, non addendi:
    # composite = quality * gate(fit) * gate(opportunity).
    relevance_floor: float = 0.25
    opportunity_floor: float = 0.15
    # Fit minimo perché a un item valga la pena spendere l'insight LLM: sotto
    # questa soglia si usa l'insight euristico (niente 7B). 0.0 = salta solo i
    # fit == 0, cioè gli item senza NESSUN match di keyword.
    insight_min_fit: float = 0.0
    # Heat "a delta": finestra (giorni) entro cui misurare la velocità tra
    # osservazioni di item_stats. Piccola = heat più reattiva ("sta salendo
    # ORA"), grande = più liscia. Deve coprire più run schedulati.
    heat_window_days: float = 3.0
    # Distanza minima (ore) tra le due osservazioni del delta: sotto, il
    # rapporto engagement/tempo amplifica il rumore e si ripiega sull'euristica.
    heat_min_span_hours: float = 2.0

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


class SchedulingConfig(BaseModel):
    """Politiche dei run non presidiati (``idea-radar run --scheduled``)."""

    # Età minima (ore) dell'ultimo run DONE perché un run schedulato lavori:
    # sotto la soglia il tick esce subito ("salto"). È la cadenza effettiva
    # dei run automatici quando il Mac è sveglio.
    min_interval_hours: float = 4.0
    # Se True, con Ollama giù o senza i modelli il run schedulato si salta
    # (ritenterà al tick dopo) invece di girare degradato: gli item entrati
    # senza embedding diventano idee-singleton permanenti.
    require_ollama: bool = True


class LifecycleConfig(BaseModel):
    """Ciclo di vita delle idee: l'archivio tiene il radar fresco."""

    # Giorni senza segnali nuovi (last_seen fermo) dopo i quali un'idea viene
    # archiviata in coda al run. 0 = ciclo di vita disattivato. Il ritorno è
    # automatico: un item nuovo che cade nell'idea la riporta in vita.
    archive_after_days: float = 14.0


class AppConfig(BaseModel):
    sources: list[SourceConfig] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    scoring: ScoringConfig
    clustering: ClusteringConfig = Field(default_factory=ClusteringConfig)
    scheduling: SchedulingConfig = Field(default_factory=SchedulingConfig)
    lifecycle: LifecycleConfig = Field(default_factory=LifecycleConfig)

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
