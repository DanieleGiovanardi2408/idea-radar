"""Modelli SQLModel di Idea Radar."""

from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import JSON, Column
from sqlmodel import Field, Relationship, SQLModel, UniqueConstraint


def utcnow() -> datetime:
    """UTC naive: SQLite non conserva la tzinfo, quindi tutto il progetto usa naive."""
    return datetime.now(UTC).replace(tzinfo=None)


class Difficulty(str, Enum):
    LOW = "low"
    MED = "med"
    HIGH = "high"


class IdeaStatus(str, Enum):
    PROCESSED = "processed"
    PROPOSED = "proposed"
    ARCHIVED = "archived"


class RunStatus(str, Enum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class IdeaItem(SQLModel, table=True):
    """Relazione molti-a-molti tra ideas e items."""

    __tablename__ = "idea_items"

    idea_id: int = Field(foreign_key="ideas.id", primary_key=True)
    item_id: int = Field(foreign_key="items.id", primary_key=True)


class Item(SQLModel, table=True):
    """Contenuto grezzo raccolto da una fonte (post, repo, thread, articolo...)."""

    __tablename__ = "items"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_items_source_external_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(index=True)
    external_id: str = Field(index=True)
    title: str
    url: str | None = None
    text: str | None = None
    author: str | None = None
    engagement_json: dict | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime | None = None
    fetched_at: datetime = Field(default_factory=utcnow)
    raw_json: dict | None = Field(default=None, sa_column=Column(JSON))
    # Vettore semantico (Ollama). Serve a raggruppare item che parlano della stessa cosa.
    embedding_json: list[float] | None = Field(default=None, sa_column=Column(JSON))

    ideas: list["Idea"] = Relationship(back_populates="items", link_model=IdeaItem)


class Topic(SQLModel, table=True):
    """Gruppo di idee semanticamente affini (es. 'agenti AI per il codice')."""

    __tablename__ = "topics"

    id: int | None = Field(default=None, primary_key=True)
    label: str
    first_seen: datetime = Field(default_factory=utcnow)
    last_seen: datetime = Field(default_factory=utcnow)
    centroid_json: list[float] | None = Field(default=None, sa_column=Column(JSON))

    ideas: list["Idea"] = Relationship(back_populates="topic")


class Idea(SQLModel, table=True):
    """Idea aggregata a partire da uno o più items semanticamente vicini."""

    __tablename__ = "ideas"

    id: int | None = Field(default=None, primary_key=True)
    label: str
    summary: str | None = None
    first_seen: datetime = Field(default_factory=utcnow)
    last_seen: datetime = Field(default_factory=utcnow)
    status: IdeaStatus = Field(default=IdeaStatus.PROCESSED)
    topic_id: int | None = Field(default=None, foreign_key="topics.id", index=True)
    # Centroide degli embedding degli item collegati: identità semantica dell'idea.
    centroid_json: list[float] | None = Field(default=None, sa_column=Column(JSON))
    # --- Stato UTENTE: azioni manuali, ortogonali a ``status`` (che è della
    # pipeline). I run non toccano MAI questi campi: un dismiss sopravvive ai
    # run successivi, un pin esclude l'idea dall'auto-archiviazione.
    pinned: bool = Field(default=False)
    dismissed_at: datetime | None = None  # scartata a mano: fuori dalle viste
    seen_at: datetime | None = None  # ultima apertura del dettaglio
    note: str | None = None  # appunto personale
    # --- Il "cosa fartene": generato dall'LLM quando l'idea supera la soglia.
    # Stabile come summary (si genera una volta, non a ogni run); None = non
    # ancora generato, quindi il run dopo ci riprova. Le mosse sono 2-3 azioni
    # concrete per sfruttare il vantaggio di sapere la cosa presto; l'angolo di
    # business (solo per le idee in cima) è un mini-caso: cliente, offerta,
    # perché ora, primo passo.
    moves_json: list[str] | None = Field(default=None, sa_column=Column(JSON))
    angle_text: str | None = None

    items: list[Item] = Relationship(back_populates="ideas", link_model=IdeaItem)
    topic: Topic | None = Relationship(back_populates="ideas")


class Score(SQLModel, table=True):
    """Punteggi di un'idea calcolati in un run."""

    __tablename__ = "scores"

    idea_id: int = Field(foreign_key="ideas.id", primary_key=True)
    run_id: int = Field(foreign_key="runs.id", primary_key=True)
    heat: float
    credibility: float
    feasibility: float
    opportunity: float
    fit: float
    composite: float
    why_text: str | None = None
    difficulty: Difficulty | None = None
    # Profilo (macro-tema) su cui è stato misurato il `fit`. Nullable: gli score
    # scritti prima dei profili non ne hanno uno, e non si inventa.
    profile: str | None = Field(default=None, index=True)


class ItemStat(SQLModel, table=True):
    """Osservazione dell'engagement di un item in un run.

    ``upsert_item`` SOVRASCRIVE ``engagement_json`` a ogni re-fetch: senza
    questa tabella la storia (stelle/punti nel tempo) andrebbe persa proprio
    ora che i run schedulati la producono da soli. È la materia prima della
    heat "a delta": velocità misurata tra osservazioni consecutive, non
    mediata sull'età dell'item (``scoring._delta_velocity``).
    """

    __tablename__ = "item_stats"

    item_id: int = Field(foreign_key="items.id", primary_key=True)
    run_id: int = Field(foreign_key="runs.id", primary_key=True)
    # Engagement grezzo osservato (per fonte: stelle/forks, punti/commenti…).
    engagement_json: dict | None = Field(default=None, sa_column=Column(JSON))
    # Riduzione scalare con la STESSA formula dello scoring: i delta si fanno
    # senza ripetere la riduzione a ogni lettura.
    engagement: float = 0.0
    observed_at: datetime = Field(default_factory=utcnow)


class WorkspaceStage(str, Enum):
    """A che punto sei TU con un'idea che hai deciso di sviluppare."""

    EXPLORE = "explore"  # da esplorare: salvata, ancora da capire
    BUILDING = "building"  # in sviluppo: ci stai lavorando
    PARKED = "parked"  # parcheggiata: non ora, non mai


class WorkspaceEntry(SQLModel, table=True):
    """Un'idea portata sul tavolo di lavoro: il piano dell'UTENTE, non del radar.

    Stessa regola di pin/dismiss: i run non toccano MAI questa tabella. Vive
    separata da ``Idea`` perché lo stato di sviluppo è un altro dominio — il
    radar osserva il mondo, qui si osserva il proprio lavoro.
    """

    __tablename__ = "workspace"

    idea_id: int = Field(foreign_key="ideas.id", primary_key=True)
    stage: WorkspaceStage = Field(default=WorkspaceStage.EXPLORE)
    # Checklist: le mosse LLM diventano to-do spuntabili al momento
    # dell'ingresso, più quelli aggiunti a mano. [{"text": str, "done": bool}]
    checklist_json: list[dict] | None = Field(default=None, sa_column=Column(JSON))
    # Collegamenti dell'utente: repo, note, prototipi. ["https://…", …]
    links_json: list[str] | None = Field(default=None, sa_column=Column(JSON))
    # Il punteggio al momento dell'ingresso: la baseline del "da quando la segui".
    composite_at_save: float = 0.0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class OutcomeVerdict(str, Enum):
    """Com'è andata un'idea DOPO che il radar l'ha proposta.

    ``NA`` = non giudicabile: nessun item su fonti live-counter, quindi
    nessun delta misurato su cui basare un verdetto onesto. Si salva comunque,
    così il calcolo non la riesamina a ogni run.
    """

    HIT = "hit"  # ha continuato a crescere: il radar aveva ragione
    FLAT = "flat"  # viva ma ferma
    MISS = "miss"  # morta lì
    NA = "na"  # non giudicabile (niente contatori vivi)


class IdeaOutcome(SQLModel, table=True):
    """Il verdetto su una previsione del radar, con i numeri che lo motivano.

    Una riga per idea: il giudizio è ricalcolabile (``--recompute``) ma non
    versionato — la storia che conta è già in ``scores`` e ``item_stats``,
    questa tabella è la lettura che ne diamo oggi.
    """

    __tablename__ = "idea_outcomes"

    idea_id: int = Field(foreign_key="ideas.id", primary_key=True)
    # Il run in cui l'idea ha superato la soglia per la prima volta: il
    # momento della "previsione", contro cui si giudica il dopo.
    promoted_run_id: int = Field(foreign_key="runs.id")
    promoted_at: datetime
    horizon_days: int
    verdict: OutcomeVerdict
    # La motivazione, in numeri: engagement/giorno prima e dopo la promozione,
    # quanto è cresciuto in totale nell'orizzonte, quanti item nuovi sono
    # arrivati dopo. Il pannello li mostra: un verdetto senza numeri è un'opinione.
    pre_velocity: float = 0.0
    post_velocity: float = 0.0
    gained: float = 0.0
    n_new_items: int = 0
    computed_at: datetime = Field(default_factory=utcnow)


class TopicStat(SQLModel, table=True):
    """Fotografia di un topic in un run: serve a misurare i trend nel tempo."""

    __tablename__ = "topic_stats"

    topic_id: int = Field(foreign_key="topics.id", primary_key=True)
    run_id: int = Field(foreign_key="runs.id", primary_key=True)
    n_items: int = 0
    n_ideas: int = 0
    avg_composite: float = 0.0


class Run(SQLModel, table=True):
    """Esecuzione della pipeline di raccolta/scoring."""

    __tablename__ = "runs"

    id: int | None = Field(default=None, primary_key=True)
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    status: RunStatus = Field(default=RunStatus.RUNNING)
    n_items: int = 0
    n_ideas_processed: int = 0
    n_ideas_proposed: int = 0
    # Progresso osservabile mentre il run gira (per il monitor live).
    phase: str = "avvio"
    n_items_fetched: int = 0
    n_items_new: int = 0
    n_ideas_total: int = 0
    n_topics: int = 0
    error: str | None = None
    sources_json: dict | None = Field(default=None, sa_column=Column(JSON))
