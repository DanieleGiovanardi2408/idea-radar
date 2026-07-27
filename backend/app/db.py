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


# Colonne aggiunte DOPO la prima release: ``create_all`` crea le tabelle nuove
# complete ma non altera quelle esistenti, quindi su un DB già in uso vanno
# aggiunte a mano. SQLite supporta solo ADD COLUMN: per questo caso basta.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "ideas": {
        "pinned": "BOOLEAN NOT NULL DEFAULT 0",
        "dismissed_at": "TIMESTAMP",
        "seen_at": "TIMESTAMP",
        "note": "TEXT",
    },
    "scores": {
        # Profilo (macro-tema) su cui è stato misurato il fit. Gli score vecchi
        # restano a NULL: nessun profilo è meglio di uno inventato.
        "profile": "VARCHAR",
    },
}


def _migrate(engine: Engine) -> None:
    """Migrazione additiva: aggiunge le colonne mancanti alle tabelle esistenti."""
    with engine.connect() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            existing = {
                row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")
            }
            if not existing:  # tabella non ancora creata: ci pensa create_all
                continue
            for column, ddl in columns.items():
                if column not in existing:
                    conn.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"
                    )
        conn.commit()


def init_db(engine: Engine | None = None) -> None:
    """Crea le tabelle se non esistono e applica le migrazioni additive."""
    engine = engine or get_engine()
    SQLModel.metadata.create_all(engine)
    _migrate(engine)


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
