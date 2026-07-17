"""I trend devono basarsi solo sui run DONE.

Un run FAILED (o ancora RUNNING) non scrive i suoi ``TopicStat``: se entrasse
nella finestra dei trend, ogni topic mostrerebbe un punto a zero — un "cratere"
finto in tutte le serie. Con i run schedulati i fallimenti non presidiati
diventano normali, quindi il filtro è parte del design dello scheduling.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlmodel import Session, create_engine

from app.db import init_db
from app.models import Idea, Run, RunStatus, Topic, TopicStat
from app.queries import topic_trends


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    with Session(engine) as session:
        yield session


def _add_run(session: Session, status: RunStatus) -> Run:
    run = Run(status=status, phase="x")
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _seed_topic(session: Session) -> Topic:
    topic = Topic(label="Agenti AI")
    session.add(topic)
    session.commit()
    session.refresh(topic)
    idea = Idea(label="Idea A", topic_id=topic.id)
    session.add(idea)
    session.commit()
    return topic


def _stat(session: Session, topic: Topic, run: Run, n_ideas: int) -> None:
    session.add(
        TopicStat(topic_id=topic.id, run_id=run.id, n_ideas=n_ideas, n_items=n_ideas)
    )
    session.commit()


def test_failed_run_does_not_create_zero_crater(session: Session) -> None:
    topic = _seed_topic(session)
    first = _add_run(session, RunStatus.DONE)
    _stat(session, topic, first, n_ideas=3)
    _add_run(session, RunStatus.FAILED)  # nessun TopicStat: run morto a metà
    last = _add_run(session, RunStatus.DONE)
    _stat(session, topic, last, n_ideas=4)

    trends = topic_trends(session)
    assert len(trends) == 1
    points = trends[0]["points"]
    assert [p["run_id"] for p in points] == [first.id, last.id]  # niente FAILED
    assert [p["n_ideas"] for p in points] == [3, 4]  # niente cratere a zero
    assert trends[0]["delta_ideas"] == 1  # delta tra i due run VERI


def test_running_run_is_not_a_trend_point(session: Session) -> None:
    topic = _seed_topic(session)
    done = _add_run(session, RunStatus.DONE)
    _stat(session, topic, done, n_ideas=2)
    _add_run(session, RunStatus.RUNNING)  # run in corso: TopicStat non ancora scritti

    trends = topic_trends(session)
    assert [p["run_id"] for p in trends[0]["points"]] == [done.id]
    assert trends[0]["n_ideas"] == 2  # l'ultimo punto resta quello del run finito
