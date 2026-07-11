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


class IdeaItem(SQLModel, table=True):
    """Relazione molti-a-molti tra ideas e items."""

    __tablename__ = "idea_items"

    idea_id: int = Field(foreign_key="ideas.id", primary_key=True)
    item_id: int = Field(foreign_key="items.id", primary_key=True)


class Item(SQLModel, table=True):
    """Contenuto grezzo raccolto da una fonte (post, repo, thread...)."""

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

    ideas: list["Idea"] = Relationship(back_populates="items", link_model=IdeaItem)


class Idea(SQLModel, table=True):
    """Idea aggregata a partire da uno o più items."""

    __tablename__ = "ideas"

    id: int | None = Field(default=None, primary_key=True)
    label: str
    summary: str | None = None
    first_seen: datetime = Field(default_factory=utcnow)
    last_seen: datetime = Field(default_factory=utcnow)
    status: IdeaStatus = Field(default=IdeaStatus.PROCESSED)

    items: list[Item] = Relationship(back_populates="ideas", link_model=IdeaItem)


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


class Run(SQLModel, table=True):
    """Esecuzione della pipeline di raccolta/scoring."""

    __tablename__ = "runs"

    id: int | None = Field(default=None, primary_key=True)
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    n_items: int = 0
    n_ideas_processed: int = 0
    n_ideas_proposed: int = 0
