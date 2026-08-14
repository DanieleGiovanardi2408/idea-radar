"""Le "mosse": il cosa-fartene delle idee sopra soglia, generato una volta sola."""

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from sqlmodel import Session, create_engine

from app.appconfig import AppConfig, MovesConfig, ScoringConfig
from app.config import Settings
from app.db import init_db
from app.llm import (
    DEFAULT_GENERIC_MOVE_PATTERNS,
    GenerationRejected,
    OllamaClient,
    OllamaError,
    generic_moves,
)
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


# ---- Validazione post-generazione (asse B del piano v2) ----------------------
#
# Il prompt vieta le mosse passe-partout da sempre e continuavano ad arrivare:
# quello che il prompt chiede va verificato DOPO, sulla risposta.


def _sequence_client(payloads: list[dict]) -> tuple[OllamaClient, list[str]]:
    """Client che risponde diversamente a chiamate successive, e registra i prompt."""
    import json as _json

    prompts: list[str] = []
    remaining = list(payloads)

    def handler(request: httpx.Request) -> httpx.Response:
        prompts.append(_json.loads(request.content)["prompt"])
        return httpx.Response(200, json={"response": _json.dumps(remaining.pop(0))})

    client = OllamaClient(
        Settings(), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    return client, prompts


class _FakeEmbedder:
    """Embedder che decide la coerenza: vettori uguali (1.0) od ortogonali (0.0)."""

    def __init__(self, coherent: list[bool], fail: bool = False) -> None:
        self.coherent = coherent
        self.fail = fail
        self.calls = 0

    def embed_many(self, texts: list[str]) -> list[list[float] | None]:
        from app.embeddings import EmbeddingError

        if self.fail:
            raise EmbeddingError("embedding giù")
        ok = self.coherent[self.calls] if self.calls < len(self.coherent) else True
        self.calls += 1
        return [[1.0, 0.0], [1.0, 0.0] if ok else [0.0, 1.0]]


GENERICA = "Monitora gli sviluppi del progetto e valutane l'adozione."
SPECIFICA = "Pubblica il confronto con Foo sui due casi che il README non copre."


def test_the_patterns_recognise_the_parroted_prompt_examples() -> None:
    """I casi visti davvero in produzione, non pattern inventati a tavolino."""
    visti = [
        "Segui gli sviluppi di questa tecnologia.",
        "Approfondisci il funzionamento del progetto.",
        "Valuta l'uso di questo strumento nel tuo stack.",
        "Scrivi la guida di riferimento.",  # l'esempio del prompt, ricopiato nudo
        "Resta aggiornato sulle novità.",
    ]
    assert generic_moves(visti, DEFAULT_GENERIC_MOVE_PATTERNS) == visti
    assert generic_moves([SPECIFICA], DEFAULT_GENERIC_MOVE_PATTERNS) == []


def test_an_invalid_pattern_is_ignored_instead_of_crashing_the_run() -> None:
    """I pattern arrivano da config.yaml: un umano di fretta non ferma un run."""
    assert generic_moves([SPECIFICA], ["(non chiusa"]) == []


def test_a_generic_move_is_dropped_and_the_good_ones_survive_without_a_retry() -> None:
    """Due buone su tre valgono già: rigenerare costerebbe 7s per riscriverle."""
    client, prompts = _sequence_client([{"moves": [GENERICA, SPECIFICA]}])

    moves = client.moves(
        "t", "s", "w", "sig", generic_patterns=DEFAULT_GENERIC_MOVE_PATTERNS
    )

    assert moves == [SPECIFICA]
    assert len(prompts) == 1  # nessuna seconda chiamata


def test_only_generic_moves_trigger_one_retry_that_says_what_was_rejected() -> None:
    client, prompts = _sequence_client(
        [{"moves": [GENERICA]}, {"moves": [SPECIFICA]}]
    )

    moves = client.moves(
        "t", "s", "w", "sig", generic_patterns=DEFAULT_GENERIC_MOVE_PATTERNS
    )

    assert moves == [SPECIFICA]
    assert len(prompts) == 2
    # Il motivo del rifiuto nel prompt: senza, il modello ripropone la stessa
    # mossa con altre parole.
    assert "RIFIUTATO" in prompts[1]
    assert GENERICA in prompts[1]
    assert client.calls_made == 2


def test_generic_twice_gives_up_without_pretending_ollama_is_down() -> None:
    client, prompts = _sequence_client([{"moves": [GENERICA]}, {"moves": [GENERICA]}])

    with pytest.raises(GenerationRejected) as caught:
        client.moves("t", "s", "w", "sig", generic_patterns=DEFAULT_GENERIC_MOVE_PATTERNS)

    assert len(prompts) == 2  # una sola rigenerazione, non un ciclo
    # Non è un OllamaError: Ollama ha risposto. La distinzione decide se la
    # fase si ferma o prosegue con le altre idee.
    assert not isinstance(caught.value, OllamaError)


def test_without_patterns_nothing_is_validated() -> None:
    """Il default è non filtrare: i pattern li porta la config, non il client."""
    assert _client({"moves": [GENERICA]}).moves("t", "s", "w", "sig") == [GENERICA]


def test_an_off_topic_angle_is_regenerated_once_with_the_idea_in_front() -> None:
    fuori_tema = "Le agenzie di viaggio potrebbero migliorare i loro processi."
    client, prompts = _sequence_client(
        [{"angle": fuori_tema}, {"angle": "Chi fa girare Foo in locale…"}]
    )

    angle = client.business_angle(
        "Foo", "s", "w", "sig", embedder=_FakeEmbedder([False, True]), min_similarity=0.35
    )

    assert angle == "Chi fa girare Foo in locale…"
    assert len(prompts) == 2
    assert fuori_tema in prompts[1]


def test_an_angle_off_topic_twice_is_rejected() -> None:
    client, _ = _sequence_client([{"angle": "di altro"}, {"angle": "ancora d'altro"}])

    with pytest.raises(GenerationRejected):
        client.business_angle(
            "Foo", "s", "w", "sig", embedder=_FakeEmbedder([False, False]), min_similarity=0.35
        )


def test_the_angle_survives_when_the_embeddings_are_down() -> None:
    """Non giudicabile ≠ colpevole: stessa regola del filtro video."""
    client, prompts = _sequence_client([{"angle": "un angolo qualunque"}])

    angle = client.business_angle(
        "Foo", "s", "w", "sig", embedder=_FakeEmbedder([], fail=True), min_similarity=0.35
    )

    assert angle == "un angolo qualunque"
    assert len(prompts) == 1


def test_the_coherence_check_is_off_at_zero() -> None:
    embedder = _FakeEmbedder([False])
    angle = _client({"angle": "qualsiasi cosa"}).business_angle(
        "Foo", "s", "w", "sig", embedder=embedder, min_similarity=0.0
    )
    assert angle == "qualsiasi cosa"
    assert embedder.calls == 0  # nemmeno interrogato


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
    """Doppio che conta le chiamate. ``cost`` = chiamate LLM per generazione.

    ``reject`` elenca le etichette per cui il modello risponde ma la risposta
    non passa la validazione: serve a distinguere "Ollama è giù" (la fase si
    ferma) da "questa risposta era generica" (si va avanti).
    """

    def __init__(
        self, fail: bool = False, reject: tuple[str, ...] = (), cost: int = 1
    ) -> None:
        self.moves_calls: list[str] = []
        self.angle_calls: list[str] = []
        self.calls_made = 0
        self._fail = fail
        self._reject = reject
        self._cost = cost

    def moves(self, label: str, summary: str, why: str, signals: str, **kwargs) -> list[str]:
        if self._fail:
            raise OllamaError("giù")
        self.calls_made += self._cost
        if label in self._reject:
            raise GenerationRejected("solo passe-partout")
        self.moves_calls.append(label)
        return [f"sfrutta {label}"]

    def business_angle(self, label: str, summary: str, why: str, signals: str, **kwargs) -> str:
        if self._fail:
            raise OllamaError("giù")
        self.calls_made += self._cost
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


def test_a_rejected_idea_does_not_stop_the_ones_after_it(session: Session) -> None:
    """Ollama giù ferma la fase; una risposta generica riguarda una sola idea."""
    run = _seed_run(session, [0.9, 0.8])
    ollama = CountingOllama(reject=("Idea 0",))

    _moves_phase(session, run, _config(angle_top_n=0), ollama, None)

    ideas = _ideas_by_label(session)
    assert ideas["Idea 0"].moves_json is None  # bocciata: ritenta al run dopo
    assert ideas["Idea 1"].moves_json == ["sfrutta Idea 1"]  # la fase è proseguita


def test_the_budget_counts_the_calls_actually_made_not_the_ideas(
    session: Session,
) -> None:
    """Una rigenerazione costa una chiamata vera: il tetto sul tempo deve vederla."""
    run = _seed_run(session, [0.9, 0.8, 0.7])
    ollama = CountingOllama(cost=2)  # ogni generazione ne è costate due

    _moves_phase(session, run, _config(angle_top_n=0, max_llm_calls_per_run=4), ollama, None)

    assert ollama.moves_calls == ["Idea 0", "Idea 1"]  # non tre


def test_the_switch_turns_the_phase_off(session: Session) -> None:
    run = _seed_run(session, [0.9])
    ollama = CountingOllama()

    _moves_phase(session, run, _config(enabled=False), ollama, None)

    assert ollama.moves_calls == []
