"""Collector Stack Exchange: misura la domanda insoddisfatta, non l'offerta."""

from datetime import datetime, timezone

import httpx

from app.appconfig import AppConfig, ScoringConfig, SourceConfig
from app.config import Settings
from app.sources.base import create_source
from app.sources.profiles import profile_for
from app.sources.stackexchange import PROFILE, StackExchangeSource

TODAY = datetime(2026, 7, 27, tzinfo=timezone.utc)


def _app_config(keywords: list[str] | None = None) -> AppConfig:
    return AppConfig(
        sources=[],
        keywords=keywords or ["ai agents", "self-hosted"],
        scoring=ScoringConfig(weights={"heat": 1.0}, threshold=0.5),
    )


def _cfg(**overrides) -> SourceConfig:
    base = dict(name="so", type="stackexchange", limit=10, tags=["langchain"])
    base.update(overrides)
    return SourceConfig(**base)


def _question(qid: int, score: int, *, answered: bool = False, **extra) -> dict:
    question = {
        "question_id": qid,
        "title": f"Come si fa la cosa {qid}?",
        "body": "<p>Ho provato <code>tutto</code> e non funziona.</p>",
        "link": f"https://stackoverflow.com/q/{qid}",
        "score": score,
        "view_count": score * 100,
        "answer_count": 0,
        "is_answered": answered,
        "creation_date": 1785000000,
        "owner": {"display_name": "tizio"},
        "tags": ["langchain", "python"],
    }
    question.update(extra)
    return question


def _source(handler, **cfg_overrides) -> StackExchangeSource:
    return StackExchangeSource(
        _cfg(**cfg_overrides),
        _app_config(),
        Settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_asks_for_recent_and_most_voted_questions() -> None:
    """`sort=votes` su una finestra recente: chi ha lo STESSO problema.

    `sort=activity` darebbe le domande con l'ultimo commento più recente, che è
    una misura di chiacchiericcio, non di domanda insoddisfatta.
    """
    src = StackExchangeSource(_cfg(max_age_days=30), _app_config(), Settings())

    params = src.query_params("langchain", 10, today=TODAY)

    assert params["sort"] == "votes"
    assert params["tagged"] == "langchain"
    assert params["site"] == "stackoverflow"
    assert params["filter"] == "withbody"  # serve il testo per embedding e insight
    expected = int((TODAY.timestamp())) - 30 * 86400
    assert abs(params["fromdate"] - expected) < 2


def test_answered_questions_are_not_opportunities() -> None:
    """Una domanda risolta ha già la sua soluzione: è documentazione."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "items": [
                    _question(1, 50, answered=True),
                    _question(2, 40, accepted_answer_id=999),
                    _question(3, 30),
                ]
            },
        )

    assert [i.external_id for i in _source(handler).fetch()] == ["3"]


def test_votes_are_a_live_counter_but_age_is_not_saturation() -> None:
    """Una domanda vecchia e votata è un problema APERTO, non un mercato chiuso.

    È la differenza con GitHub: là "popolare e vecchio" significa saturo, qui
    significa che il problema resiste da tempo — se possibile, vale di più.
    """
    assert profile_for("stackexchange") is PROFILE
    assert PROFILE.live_counter is True
    assert PROFILE.maturity_in_saturation is False
    # I cap sono tarati sulla scala di SO: 50 voti sono tanti, non pochi.
    assert PROFILE.velocity_cap < 10
    assert PROFILE.engagement({"score": 20, "views": 5_000, "answers": 2}) == 31.0


def test_tags_fall_back_to_the_keywords() -> None:
    """Senza tag espliciti si derivano dalle keyword, con i trattini di SO."""
    src = StackExchangeSource(_cfg(tags=[]), _app_config(), Settings())
    assert src.search_tags() == ["ai-agents", "self-hosted"]


def test_one_request_per_tag_and_dedup() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["tagged"])
        return httpx.Response(200, json={"items": [_question(7, 5)]})

    items = _source(handler, tags=["langchain", "rag", "ollama"]).fetch()

    assert seen == ["langchain", "rag", "ollama"]
    assert [i.external_id for i in items] == ["7"]  # la stessa domanda una volta


def test_body_is_stripped_and_tags_are_kept_for_the_fit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [_question(11, 9)]})

    item = _source(handler).fetch()[0]

    assert "<p>" not in item.text and "<code>" not in item.text
    assert "[langchain, python]" in item.text  # i tag aiutano fit e clustering
    assert item.created_at == datetime.fromtimestamp(1785000000, tz=timezone.utc).replace(
        tzinfo=None
    )
    assert item.created_at.tzinfo is None


def test_an_exhausted_quota_does_not_kill_the_run(caplog) -> None:
    """300 richieste al giorno senza chiave: la quota va notata, non subita."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [_question(1, 3)], "quota_remaining": 0})

    with caplog.at_level("WARNING"):
        items = _source(handler).fetch()

    assert len(items) == 1
    assert "quota" in caplog.text.lower()


def test_a_failing_tag_does_not_kill_the_others() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["tagged"] == "langchain":
            return httpx.Response(400, json={"error_message": "throttled"})
        return httpx.Response(200, json={"items": [_question(42, 12)]})

    items = _source(handler, tags=["langchain", "rag"]).fetch()

    assert [i.external_id for i in items] == ["42"]


def test_registered_in_the_source_registry() -> None:
    src = create_source(_cfg(), _app_config(), Settings())
    assert isinstance(src, StackExchangeSource)
