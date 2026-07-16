import json

import httpx
import pytest

from app.config import Settings
from app.llm import (
    OllamaClient,
    OllamaError,
    _parse_difficulty,
    generate_insight,
)
from app.models import Difficulty, Item


def _item() -> Item:
    return Item(source="hn", external_id="1", title="titolo", text="corpo", engagement_json={})


def test_parse_difficulty_aliases_and_invalids() -> None:
    assert _parse_difficulty("medium") == Difficulty.MED
    assert _parse_difficulty("HIGH") == Difficulty.HIGH
    assert _parse_difficulty("low") == Difficulty.LOW
    assert _parse_difficulty("boh") is None
    assert _parse_difficulty(3) is None


def test_ollama_insight_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = {"summary": "s", "why_text": "w", "difficulty": "low"}
        return httpx.Response(200, json={"response": json.dumps(payload)})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    insight = OllamaClient(Settings(), client=client).insight(_item())
    assert insight.summary == "s"
    assert insight.why_text == "w"
    assert insight.difficulty == Difficulty.LOW


def test_ollama_insight_raises_on_http_error() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(OllamaError):
        OllamaClient(Settings(), client=client).insight(_item())


def test_generate_insight_falls_back_when_not_required() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    ollama = OllamaClient(Settings(), client=client)
    insight = generate_insight(_item(), Settings(llm_required=False), ollama=ollama)
    assert insight.why_text.startswith("Segnale")
    assert insight.difficulty is None


def test_generate_insight_raises_when_required() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    ollama = OllamaClient(Settings(), client=client)
    with pytest.raises(OllamaError):
        generate_insight(_item(), Settings(llm_required=True), ollama=ollama)
