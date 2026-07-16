"""Modelli SQLModel di Idea Radar."""

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import JSON, Column
from sqlmodel import Field, Relationship, SQLModel, UniqueConstraint


def utcnow() -> datetime:
    """UTC naive: SQLite non conserva la tzinfo, quindi tutto il progetto usa naive."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
