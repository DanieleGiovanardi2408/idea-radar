"""Layer di storage SQLite."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, event
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Item

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "idea_radar.db"

_engine: Engine | None = None


def _sqlite_on_connect(dbapi_connection, _record) -> None:
    """PRAGMA di concorrenza, applicati a ogni nuova connessione.

    Coi run schedulati la pipeline scrive MENTRE l'API/UI legge: WAL lascia
    procedere i lettori durante uno scrittore, e il busy_timeout assorbe i
    brevi lock residui invece di esplodere subito con "database is locked".
    (WAL crea i file di servizio ``-wal``/``-shm`` accanto al DB, già coperti
    dal .gitignore di ``data/``.)
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


def make_engine(db_path: Path) -> Engine:
    """Engine SQLite con i PRAGMA già agganciati (riusabile nei test)."""
    engine = create_engine(f"sqlite:///{db_path}")
    event.listens_for(engine, "connect")(_sqlite_on_connect)
    return engine


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _engine = make_engine(DB_PATH)
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
