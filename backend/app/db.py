"""Layer di storage SQLite."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Item

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "idea_radar.db"

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(f"sqlite:///{DB_PATH}")
    return _engine


def init_db(engine: Engine | None = None) -> None:
    """Crea le tabelle se non esistono."""
    SQLModel.metadata.create_all(engine or get_engine())


@contextmanager
def get_session(engine: Engine | None = None) -> Iterator[Session]:
    with Session(engine or get_engine()) as session:
        yield session


# Campi aggiornati quando un item già presente viene ri-fetchato.
_ITEM_UPDATABLE_FIELDS = (
    "title",
    "url",
    "text",
    "author",
    "engagement_json",
    "created_at",
    "fetched_at",
    "raw_json",
)


def upsert_item(session: Session, item: Item) -> Item:
    """Inserisce l'item o, se (source, external_id) esiste già, lo aggiorna."""
    existing = session.exec(
        select(Item).where(
            Item.source == item.source,
            Item.external_id == item.external_id,
        )
    ).first()

    if existing is None:
        session.add(item)
        session.commit()
        session.refresh(item)
        return item

    for field in _ITEM_UPDATABLE_FIELDS:
        setattr(existing, field, getattr(item, field))
    session.add(existing)
    session.commit()
    session.refresh(existing)
    return existing
