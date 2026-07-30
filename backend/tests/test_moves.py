"""Le "mosse": il cosa-fartene delle idee sopra soglia, generato una volta sola."""

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from sqlmodel import Session, create_engine

from app.appconfig import AppConfig, MovesConfig, ScoringConfig
from app.config import Settings
from app.db import init_db
from app.llm import OllamaClient, OllamaError
from app.models import Idea, IdeaStatus, Item, Run, RunStatus, Score
from app.pipeline import _moves_phase


# ---- Parsing delle risposte del modello -------------------------------------


def _client(payload: dict | str) -> OllamaClient:
    import json as _json

    def handler(request: httpx.Request) -> httpx.Response:
        body = payload if isinstance(payload, str) else _json.dumps(payload)
        return httpx.Response(200, json={"response": body})

    return OllamaClient(
        Settings(), client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_moves_are_parsed_trimmed_and_capped_at_three() -> None:
    client = _client({"moves": ["  Costruisci X  ", "Scrivi Y", "", "Integra Z", "Quarta"]})
    assert client.moves("t", "s", "w", "sig") == ["Costruisci X", "Scrivi Y", "Integra Z"]


def test_moves_that_are_not_a_list_raise() -> None:
    with pytest.raises(OllamaError):
        _client({"moves": "una stringa sola"}).moves("t", "s", "w", "sig")


def test_empty_moves_raise_instead_of_saving_nothing() -> None:
    """Una lista vuota salvata varrebbe 'già generato': non deve succedere."""
    with pytest.raises(OllamaError):
        _client({"moves": []}).moves("t", "s", "w", "sig")


def test_the_angle_is_parsed_and_an_empty_one_raises() -> None:
    assert _client({"angle": " Il cliente è X. "}).business_angle("t", "s", "w", "sig") == "Il cliente è X."
    with pytest.raises(OllamaError):
        _client({"angle": ""}).business_angle("t", "s", "w", "sig")


# ---- La fase in pipeline -----------------------------------------------------


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    with Session(engine) as session:
        yield session


def _config(**moves) -> AppConfig:
    return AppConfig(
        sources=[],
        keywords=["ai"],
        scoring=ScoringConfig(weights={"heat": 1.0}, threshold=0.5),
        moves=MovesConfig(**moves),
    )


def _seed_run(session: Session, composites: list[float]) -> Run:
    """Un run con un'idea per composite dato; sopra soglia = >= 0.5."""
    run = Run(status=RunStatus.DONE, phase="completato")
    session.add(run)
    session.commit()
    session.refresh(run)
    for n, composite in enumerate(composites):
        idea = Idea(label=f"Idea {n}", summary=f"riassunto {n}", status=IdeaStatus.PROPOSED)
        idea.items = [Item(source="hn", external_id=str(n), title=f"Idea {n}")]
        session.add(idea)
        session.commit()
        session.refresh(idea)
        session.add(
            Score(
                idea_id=idea.id,
                run_id=run.id,
                heat=0.5,
                credibility=0.5,
                feasibility=0.5,
                opportunity=0.5,
                fit=0.5,
                composite=composite,
                why_text=f"perché {n}",
            )
        )
    session.commit()
    return run


class CountingOllama:
    def __init__(self, fail: bool = False) -> None:
        self.moves_calls: list[str] = []
        self.angle_calls: list[str] = []
        self._fail = fail

    def moves(self, label: str, summary: str, why: str, signals: str) -> list[str]:
        if self._fail:
            raise OllamaError("giù")
        self.moves_calls.append(label)
        return [f"sfrutta {label}"]

    def business_angle(self, label: str, summary: str, why: str, signals: str) -> str:
        if self._fail:
            raise OllamaError("giù")
        self.angle_calls.append(label)
        return f"angolo per {label}"


def _ideas_by_label(session: Session) -> dict[str, Idea]:
    from sqlmodel import select

    return {i.label: i for i in session.exec(select(Idea)).all()}


def test_only_ideas_above_threshold_get_moves(session: Session) -> None:
    run = _seed_run(session, [0.8, 0.3])  # una sopra, una sotto
    ollama = CountingOllama()

    _moves_phase(session, run, _config(), ollama, None)

    ideas = _ideas_by_label(session)
    assert ideas["Idea 0"].moves_json == ["sfrutta Idea 0"]
    assert ideas["Idea 1"].moves_json is None  # sotto soglia: non vale il 7B


def test_the_angle_goes_only_to_the_top_n(session: Session) -> None:
    run = _seed_run(session, [0.9, 0.8, 0.7])
    ollama = CountingOllama()

    _moves_phase(session, run, _config(angle_top_n=1), ollama, None)

    ideas = _ideas_by_label(session)
    assert ideas["Idea 0"].angle_text == "angolo per Idea 0"  # la più calda
    assert ideas["Idea 1"].angle_text is None
    assert ideas["Idea 1"].moves_json is not None  # le mosse però le ha


def test_moves_are_generated_once_like_summary(session: Session) -> None:
    run = _seed_run(session, [0.8])
    ollama = CountingOllama()
    _moves_phase(session, run, _config(), ollama, None)
    _moves_phase(session, run, _config(), ollama, None)  # secondo run: cache

    assert len(ollama.moves_calls) == 1


def test_the_budget_caps_the_llm_calls(session: Session) -> None:
    run = _seed_run(session, [0.9, 0.8, 0.7, 0.6])
    ollama = CountingOllama()

    _moves_phase(session, run, _config(angle_top_n=0, max_llm_calls_per_run=2), ollama, None)

    assert len(ollama.moves_calls) == 2  # le altre due aspettano il run dopo


def test_ollama_down_leaves_null_and_stops_insisting(session: Session) -> None:
    """Niente fallback euristico: una mossa passe-partout è peggio di nessuna."""
    run = _seed_run(session, [0.9, 0.8])

    _moves_phase(session, run, _config(), CountingOllama(fail=True), None)

    ideas = _ideas_by_label(session)
    assert all(i.moves_json is None for i in ideas.values())  # riproverà


def test_the_switch_turns_the_phase_off(session: Session) -> None:
    run = _seed_run(session, [0.9])
    ollama = CountingOllama()

    _moves_phase(session, run, _config(enabled=False), ollama, None)

    assert ollama.moves_calls == []
