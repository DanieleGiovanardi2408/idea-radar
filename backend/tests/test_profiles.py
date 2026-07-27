"""Profili: i temi del radar, con un `fit` ciascuno.

Il difetto che risolvono: con un unico insieme di keyword globali, un'idea di
domotica veniva misurata anche su "prompt engineering" e prendeva un voto medio
che non distingue "fuori tema" da "a metà". Separando per profilo la domanda
diventa la giusta — centrale per uno, irrilevante per gli altri — e il profilo
vincente è anche il macro-tema dell'idea, dichiarato e non indovinato.
"""

import pytest

from app.appconfig import (
    IMPLICIT_PROFILE,
    AppConfig,
    ProfileConfig,
    ScoringConfig,
    SourceConfig,
)
from app.llm import IdeaInsight
from app.models import Item
from app.scoring import best_profile, profile_fits, score_item


def _config(profiles: list[ProfileConfig] | None = None) -> AppConfig:
    return AppConfig(
        sources=[],
        keywords=["developer tools", "automation"],
        profiles=profiles or [],
        scoring=ScoringConfig(weights={"heat": 1.0}, threshold=0.5),
    )


PROFILES = [
    ProfileConfig(name="ai-agents", label="Agenti AI", keywords=["ai agents", "mcp server"]),
    ProfileConfig(name="domotica", label="Domotica", keywords=["home assistant", "esp32"]),
]


def _item(title: str, text: str = "") -> Item:
    return Item(source="hn", external_id=title, title=title, text=text)


def test_fit_is_measured_per_profile_not_averaged() -> None:
    """Il cuore della cosa: un'idea può essere centrale per un tema e non per altri."""
    item = _item("Show HN: un dashboard per home assistant su esp32")

    fits = profile_fits(item, _config(PROFILES))

    assert fits["domotica"] == 1.0
    assert fits["ai-agents"] == 0.0
    # E il fit usato dallo scoring è quello del tema giusto, non la media.
    assert best_profile(item, _config(PROFILES)) == ("domotica", 1.0)


def test_the_winning_profile_ends_up_in_the_score() -> None:
    """È il macro-tema dell'idea, e va persistito per poter filtrare il radar."""
    item = _item("un mcp server per ai agents")
    result = score_item(
        item, IdeaInsight(summary="", why_text="", difficulty=None), _config(PROFILES)
    )
    assert result.profile == "ai-agents"
    assert result.fit == 1.0


def test_nobody_claims_an_off_topic_item() -> None:
    """Nessun tema, non "il primo della lista".

    Col `max` su tutti fit a zero vinceva sempre il primo profilo di config.yaml:
    sull'archivio reale 1371 idee su 1586 finivano etichettate "ai-agents" senza
    avere niente a che fare con gli agenti. Un'etichetta comoda è una bugia.
    """
    item = _item("ricetta della carbonara")

    fits = profile_fits(item, _config(PROFILES))
    assert set(fits.values()) == {0.0}

    name, fit = best_profile(item, _config(PROFILES))
    assert name is None
    assert fit == 0.0


def test_a_phrase_needs_all_its_words() -> None:
    """Regressione: "home automation" non deve matchare su "automation".

    Prima bastava una parola qualsiasi della keyword, quindi qualunque articolo
    sull'automazione prendeva punti come se fosse di domotica — e col profilo
    "domotica" che reclamava articoli di disaster recovery si vedeva benissimo.
    """
    config = _config([ProfileConfig(name="domotica", keywords=["home automation"])])

    parziale = _item("Workflow automation for teams")
    completo = _item("Home automation con Raspberry Pi")

    assert profile_fits(parziale, config)["domotica"] == 0.0
    assert profile_fits(completo, config)["domotica"] == 1.0


def test_without_profiles_the_radar_stays_monothematic() -> None:
    """La via di fuga: una configurazione vecchia continua a funzionare."""
    config = _config()
    profiles = config.effective_profiles()

    assert len(profiles) == 1
    assert profiles[0].name == IMPLICIT_PROFILE
    assert profiles[0].keywords == ["developer tools", "automation"]

    result = score_item(
        _item("developer tools per tutti"),
        IdeaInsight(summary="", why_text="", difficulty=None),
        config,
    )
    assert result.profile == IMPLICIT_PROFILE


def test_search_keywords_alternates_between_profiles() -> None:
    """Un tetto deve ridurre la profondità di ogni tema, non farne sparire uno.

    Concatenando i profili, un tetto di 2 avrebbe interrogato solo il primo tema
    e reso l'altro invisibile alle fonti che cercano per keyword.
    """
    config = _config(PROFILES)

    assert config.search_keywords() == [
        "ai agents",
        "home assistant",
        "mcp server",
        "esp32",
    ]
    assert config.search_keywords(limit=2) == ["ai agents", "home assistant"]


def test_duplicate_keywords_cost_one_request() -> None:
    config = _config(
        [
            ProfileConfig(name="a", keywords=["rag", "llm"]),
            ProfileConfig(name="b", keywords=["rag", "agenti"]),
        ]
    )
    assert config.search_keywords() == ["rag", "llm", "agenti"]


def test_a_profile_without_keywords_is_refused() -> None:
    """Un profilo che non sa dire cosa cerca non può calcolare un fit."""
    with pytest.raises(ValueError):
        ProfileConfig(name="vuoto", keywords=[])
    with pytest.raises(ValueError):
        ProfileConfig(name="spazi", keywords=["   "])


def test_label_falls_back_to_the_name() -> None:
    assert ProfileConfig(name="dev-infra", keywords=["x"]).title == "dev-infra"
    assert ProfileConfig(name="d", label="Dev infra", keywords=["x"]).title == "Dev infra"


def test_the_real_config_has_coherent_profiles() -> None:
    """Il config.yaml del progetto deve reggere le sue stesse regole."""
    from app.appconfig import get_config

    config = get_config()
    profiles = config.effective_profiles()

    assert len(profiles) >= 2
    assert len({p.name for p in profiles}) == len(profiles)  # nomi unici
    # Le fonti che costano una richiesta per keyword vanno tenute a bada.
    for source in config.enabled_sources():
        if source.type in {"huggingface", "npm", "stackexchange", "hn_algolia"}:
            requests = len(config.search_keywords(source.max_keywords))
            assert requests <= 10, f"{source.name}: {requests} richieste per run"