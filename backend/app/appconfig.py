"""Loader della configurazione comportamentale letta da ``config.yaml``.

Distinto da ``app.config`` (che legge i *segreti* dall'ambiente/.env):
qui vive il *comportamento* della pipeline — fonti, keyword, scoring.
"""

import os
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

# Come DATA_DIR in db.py: l'app desktop punta a una copia utente del config
# (modificabile e persistente), il default resta il file accanto al codice.
CONFIG_PATH = Path(
    os.environ.get("IDEA_RADAR_CONFIG")
    or Path(__file__).resolve().parent.parent / "config.yaml"
)


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
    # Tetto a UNA singola attesa quando la fonte incontra un rate limit. GitHub
    # dice sempre quando riprovare (Retry-After o x-ratelimit-reset) e aspettare
    # è meglio che perdere una fascia d'età intera; ma se il reset è lontano
    # mezz'ora, un run non deve restare appeso: oltre il tetto si rinuncia a
    # quella query e la perdita finisce nel report del Monitor.
    max_wait_seconds: float = 90.0
    # Tetto alle keyword interrogate, per le fonti che costano una richiesta a
    # keyword. 0 = nessun tetto. I profili si alternano, quindi un tetto basso
    # riduce la profondità di ogni tema senza farne sparire nessuno.
    max_keywords: int = 0


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
    threshold: float = 0.28
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


class ThroughputConfig(BaseModel):
    """Quanto lavoro per round-trip. Non cambia i risultati, solo i tempi."""

    # Testi per richiesta di embedding (`/api/embed` accetta una lista). Il
    # modello li elabora comunque in sequenza: qui si risparmiano latenza e
    # overhead HTTP, non calcolo. Alzarlo oltre il centinaio non serve e rende i
    # timeout più probabili; 1 ripristina il comportamento una-richiesta-per-item.
    embed_batch_size: int = 32
    # Secondi minimi tra due scritture dell'avanzamento. Il Monitor legge la
    # fase dal DB, quindi ogni aggiornamento è una transazione: a item lenti
    # (LLM) non si nota, ma quando gli insight arrivano dalla cache si scriveva
    # il DB decine di volte al secondo per cambiare una stringa. 0 = scrivi
    # sempre, come prima.
    #
    # NB: i commit degli score restano uno per item, di proposito. Accorparli
    # allungherebbe la transazione di scrittura a decine di secondi e con
    # `busy_timeout=30000` (db.py) un PATCH dell'API — pin, dismiss, nota —
    # arriverebbe a scadere mentre un run gira.
    progress_min_seconds: float = 1.0


class MovesConfig(BaseModel):
    """Le "mosse": cosa fartene di un'idea, generato quando supera la soglia.

    Il radar dice cosa sta salendo; le mosse dicono come sfruttare il
    vantaggio di saperlo presto. Solo per le idee sopra soglia (le altre non
    valgono il 7B), e una volta sola per idea — come summary/why.
    """

    enabled: bool = True
    # Quante idee in cima (per composite del run) ricevono anche l'angolo di
    # business, oltre alle mosse. È la parte più costosa e più letta: poche.
    angle_top_n: int = 5
    # Tetto alle chiamate LLM della fase per run (~7s l'una): le idee oltre il
    # budget restano a NULL e vengono riprese al run successivo.
    max_llm_calls_per_run: int = 12


class EnrichmentConfig(BaseModel):
    """Arricchimento degli item raccolti con segnali esterni gratuiti.

    Non è una fonte: non porta item nuovi, aggiunge trazione misurata a quelli
    che già ci sono. Il primo enricher è pypistats.org — un item che cita un
    pacchetto PyPI (link a pypi.org o `pip install X` nel testo) riceve i
    download dell'ultima settimana, un asse di engagement indipendente da
    stelle e punti.
    """

    # Interruttore dell'enricher PyPI. API pubblica e gratuita, nessuna chiave.
    pypi_downloads: bool = True
    # Tetto ai pacchetti interrogati in un run (una richiesta per pacchetto,
    # con cache dentro il run). pypistats non pubblica un rate limit formale:
    # il tetto è la nostra buona educazione.
    max_packages_per_run: int = 40
    # Download/settimana che valgono heat = 1.0, come la velocity_cap di npm:
    # i download settimanali sono già una velocità, non si divide per l'età.
    pypi_week_cap: float = 20_000.0


class VideosConfig(BaseModel):
    """Il pannello video: pertinenza prima di tutto.

    ``order=viewCount`` su keyword generiche pesca anche contenuto virale
    fuori tema (Peppa Pig su "smart home", storicamente). Due difese: la
    similarità embedding tra titolo e keyword del tema, e una blocklist di
    canali per i recidivi.
    """

    # Similarità coseno minima tra il titolo del video e le keyword del suo
    # tema (stesso modello di embedding del clustering). 0 = filtro spento.
    # La soglia giusta dipende dal modello: parte prudente, si tara guardando
    # cosa scarta nel log.
    min_similarity: float = 0.4
    # Canali esclusi a prescindere (match per sottostringa, case-insensitive).
    blocked_channels: list[str] = Field(default_factory=list)


class OutcomesConfig(BaseModel):
    """Il radar che si valuta: verdetti sulle idee proposte in passato.

    Un'idea promossa ad almeno ``horizon_days`` giorni fa viene giudicata
    guardando l'engagement dei suoi item (solo fonti live-counter, dove il
    delta è misurato) nella finestra successiva alla promozione.
    """

    # Giorni di attesa prima del giudizio: sotto, il "dopo" non è ancora
    # abbastanza lungo per distinguere una pausa da una morte.
    horizon_days: int = 30
    # hit: la velocity dopo la promozione conserva almeno questa frazione di
    # quella prima. La crescita fisiologicamente decelera: chiedere il 50%
    # a un mese di distanza è già selettivo.
    hit_ratio: float = 0.5
    # miss: la velocity dopo è sotto questa frazione di quella prima E non
    # sono arrivati item nuovi. Tra miss e hit: flat.
    miss_ratio: float = 0.1
    # Senza una velocity "prima" misurabile si giudica in assoluto: hit se
    # nell'orizzonte l'idea ha guadagnato almeno tanto engagement così.
    min_abs_gain: float = 25.0
    # Ore minime tra la prima e l'ultima osservazione perché una velocity
    # sia una misura e non rumore (stessa filosofia di heat_min_span_hours).
    min_span_hours: float = 12.0


class LifecycleConfig(BaseModel):
    """Ciclo di vita delle idee: l'archivio tiene il radar fresco."""

    # Giorni senza segnali nuovi (last_seen fermo) dopo i quali un'idea viene
    # archiviata in coda al run. 0 = ciclo di vita disattivato. Il ritorno è
    # automatico: un item nuovo che cade nell'idea la riporta in vita.
    archive_after_days: float = 14.0


class ProfileConfig(BaseModel):
    """Un tema del radar: il suo nome e le parole che lo definiscono.

    I profili sono l'unità di *rilevanza*: il fit si calcola per profilo, quindi
    un'idea può essere centrale per uno e fuori tema per un altro invece di
    ricevere un unico voto medio. Il profilo vincente è anche il macro-tema
    dell'idea, dichiarato in configurazione e non indovinato da un modello.
    """

    name: str  # identificatore stabile, usato nelle API e nel DB
    label: str = ""  # come si legge nella UI; se vuoto si usa `name`
    keywords: list[str] = Field(default_factory=list)

    @property
    def title(self) -> str:
        return self.label or self.name

    @field_validator("keywords")
    @classmethod
    def _non_empty(cls, v: list[str]) -> list[str]:
        cleaned = [k.strip() for k in v if k.strip()]
        if not cleaned:
            raise ValueError("un profilo senza keyword non può calcolare il fit")
        return cleaned


# Nome del profilo implicito quando `profiles` non è configurato: il radar si
# comporta come prima, con un tema solo che raccoglie tutte le keyword globali.
IMPLICIT_PROFILE = "tutto"


class AppConfig(BaseModel):
    sources: list[SourceConfig] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    profiles: list[ProfileConfig] = Field(default_factory=list)
    scoring: ScoringConfig
    clustering: ClusteringConfig = Field(default_factory=ClusteringConfig)
    scheduling: SchedulingConfig = Field(default_factory=SchedulingConfig)
    lifecycle: LifecycleConfig = Field(default_factory=LifecycleConfig)
    throughput: ThroughputConfig = Field(default_factory=ThroughputConfig)
    enrichment: EnrichmentConfig = Field(default_factory=EnrichmentConfig)
    moves: MovesConfig = Field(default_factory=MovesConfig)
    outcomes: OutcomesConfig = Field(default_factory=OutcomesConfig)
    videos: VideosConfig = Field(default_factory=VideosConfig)

    def enabled_sources(self) -> list[SourceConfig]:
        return [s for s in self.sources if s.enabled]

    def effective_profiles(self) -> list[ProfileConfig]:
        """I profili con cui lavorare: quelli configurati, o uno implicito.

        Senza profili il radar resta monotematico come prima — è la via di fuga
        che tiene funzionante una configurazione vecchia senza toccarla.
        """
        if self.profiles:
            return self.profiles
        return [
            ProfileConfig(
                name=IMPLICIT_PROFILE,
                label="Tutto",
                keywords=self.keywords or ["software"],
            )
        ]

    def search_keywords(self, limit: int = 0) -> list[str]:
        """Le parole con cui interrogare le fonti che cercano per keyword.

        I profili si **alternano** invece di essere concatenati: con un tetto di
        8 parole si prendono le prime due di ciascun profilo, non le otto del
        primo. Le fonti che costano una richiesta per keyword hanno bisogno di un
        tetto (18 keyword × 3 fasce su GitHub sarebbero 54 richieste, oltre il
        rate limit), e un tetto che tagli via gli ultimi profili renderebbe quei
        temi invisibili.

        ``limit=0`` significa nessun tetto. L'ordine è stabile e senza duplicati.
        """
        profiles = self.effective_profiles()
        ordered: dict[str, None] = {}
        depth = max((len(p.keywords) for p in profiles), default=0)
        for index in range(depth):
            for profile in profiles:
                if index < len(profile.keywords):
                    ordered.setdefault(profile.keywords[index], None)
        keywords = list(ordered)
        return keywords[:limit] if limit > 0 else keywords


def load_config(path: Path | None = None) -> AppConfig:
    """Carica e valida ``config.yaml``."""
    cfg_path = path or CONFIG_PATH
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(data)


@lru_cache
def get_config() -> AppConfig:
    return load_config()
