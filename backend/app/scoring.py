"""Scoring euristico delle idee.

Idea di fondo: un radar non deve premiare ciò che *ha già vinto*, ma ciò che
**sta salendo**. Quindi:

- ``heat`` misura la **velocità**, non la popolarità assoluta: n8n con 100k
  stelle in 6 anni non è un'opportunità, un repo con 2k stelle in 3 mesi sì.
  Dove la storia esiste (osservazioni ripetute in ``item_stats``) la velocità
  è **misurata a delta** tra osservazioni; altrimenti si stima con
  l'euristica engagement/età (cold start).
- ``saturation`` misura quanto una cosa è *già affermata* (popolarità assoluta
  + età) e **abbassa** l'opportunity: è il freno che tiene i progetti maturi
  fuori dalla cima.
- ``fit`` non entra nella media pesata: è un **moltiplicatore** di rilevanza,
  così un'idea fuori tema viene abbattuta anche se popolare.

    composite = quality * (relevance_floor + (1 - relevance_floor) * fit)
"""

import math
import re
from collections.abc import Sequence
from datetime import timedelta

from pydantic import BaseModel

from app.appconfig import AppConfig
from app.llm import IdeaInsight
from app.models import Difficulty, IdeaStatus, Item, ItemStat, utcnow

_QUALITY_METRICS = ("heat", "credibility", "feasibility", "opportunity")

# Velocità che vale heat = 1.0, per fonte.
_VELOCITY_CAP = {"github": 30.0, "hn": 300.0}  # stelle/giorno | punti+commenti
_DEFAULT_VELOCITY_CAP = 100.0

# Fonti il cui engagement è un CONTATORE VIVO (stelle, punti): lì un delta tra
# osservazioni misura crescita reale. I feed RSS invece fotografano il valore
# alla pubblicazione e non lo aggiornano mai: il loro delta è zero per
# costruzione, non perché l'item si sia fermato — quindi restano sull'euristica.
_LIVE_COUNTER_SOURCES = frozenset({"github", "hn"})

# Popolarità assoluta oltre la quale una cosa è "affermata".
_SATURATION_CAP = {"github": 60_000.0, "hn": 1_500.0}
_DEFAULT_SATURATION_CAP = 2_000.0

_SOURCE_CREDIBILITY = {"hn": 0.35, "github": 0.45, "rss": 0.40}
_DIFFICULTY_FEASIBILITY = {
    Difficulty.LOW: 0.80,
    Difficulty.MED: 0.55,
    Difficulty.HIGH: 0.30,
}
_WORD_RE = re.compile(r"[a-z0-9]+")


class ScoreResult(BaseModel):
    heat: float
    credibility: float
    feasibility: float
    opportunity: float
    fit: float
    composite: float
    status: IdeaStatus


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _saturate(x: float, cap: float) -> float:
    """Compressione logaritmica in [0, 1]: cresce presto, poi si appiattisce."""
    if x <= 0 or cap <= 0:
        return 0.0
    return min(1.0, math.log10(1 + x) / math.log10(1 + cap))


def _age_days(item: Item) -> float:
    if item.created_at is None:
        return 30.0  # assunzione prudente quando la data manca
    return max((utcnow() - item.created_at).total_seconds() / 86400.0, 0.5)


def absolute_engagement(item: Item) -> float:
    """Riduzione scalare dell'engagement, per fonte (pubblica: la usa anche
    la pipeline per fotografare l'engagement di ogni run in ``ItemStat``)."""
    e = item.engagement_json or {}
    if item.source == "hn":
        return float(e.get("score", 0) + e.get("comments", 0))
    if item.source == "github":
        return float(e.get("stars", 0) + 2 * e.get("forks", 0))
    return float(sum(v for v in e.values() if isinstance(v, (int, float))))


def _velocity(item: Item) -> float:
    """Stima *euristica* della velocità, quando la storia non c'è (cold start).

    Su GitHub dividiamo per l'età del repo (stelle/giorno medie di vita). Su HN
    e RSS no: la front page è per costruzione fresca, quindi l'engagement
    grezzo è già di fatto una misura di velocità.
    """
    absolute = absolute_engagement(item)
    if item.source == "github":
        return absolute / _age_days(item)
    return absolute


def _delta_velocity(
    observations: Sequence[ItemStat],
    *,
    window_days: float,
    min_span_hours: float,
) -> float | None:
    """Velocità MISURATA: engagement/giorno tra la più vecchia osservazione
    nella finestra e la più recente.

    La finestra tiene la misura "attuale": un repo cresciuto un mese fa ma
    fermo negli ultimi giorni deve raffreddarsi, non vivere di rendita sulla
    media di vita. ``None`` = segnale insufficiente (meno di due osservazioni
    nella finestra, o troppo ravvicinate perché il rapporto non sia rumore):
    il chiamante ripiega sull'euristica.
    """
    if len(observations) < 2:
        return None
    ordered = sorted(observations, key=lambda o: o.observed_at)
    last = ordered[-1]
    cutoff = last.observed_at - timedelta(days=window_days)
    windowed = [o for o in ordered if o.observed_at >= cutoff]
    if len(windowed) < 2:
        return None
    first = windowed[0]
    span_days = (last.observed_at - first.observed_at).total_seconds() / 86400.0
    if span_days <= 0 or span_days * 24.0 < min_span_hours:
        return None
    return max(0.0, (last.engagement - first.engagement) / span_days)


def _heat(
    item: Item,
    config: AppConfig,
    observations: Sequence[ItemStat] | None = None,
) -> float:
    """Heat "a delta" dove possibile, euristica dove la storia non c'è ancora."""
    cap = _VELOCITY_CAP.get(item.source, _DEFAULT_VELOCITY_CAP)
    if observations and item.source in _LIVE_COUNTER_SOURCES:
        measured = _delta_velocity(
            observations,
            window_days=config.scoring.heat_window_days,
            min_span_hours=config.scoring.heat_min_span_hours,
        )
        if measured is not None:
            return _saturate(measured, cap)
    return _saturate(_velocity(item), cap)


def _saturation(item: Item) -> float:
    """Quanto la cosa è già affermata: alta = mercato chiuso, non opportunità."""
    cap = _SATURATION_CAP.get(item.source, _DEFAULT_SATURATION_CAP)
    popularity = _saturate(absolute_engagement(item), cap)
    if item.source != "github":
        return popularity
    # Un repo è "maturo" se è popolare *e* vecchio: 2 anni satura il fattore età.
    maturity = _clamp(_age_days(item) / 730.0)
    return _clamp(popularity * (0.4 + 0.6 * maturity))


def _fit(item: Item, keywords: list[str]) -> float:
    """Rilevanza per keyword con match su *parole intere* (no sottostringhe).

    Una keyword conta come matchata se una qualsiasi delle sue parole compare
    come token intero nel testo (così 'ai' non matcha 'certain').
    """
    if not keywords:
        return 0.5
    haystack = f"{item.title} {item.text or ''}".lower()
    tokens = set(_WORD_RE.findall(haystack))
    matched = sum(
        1
        for kw in keywords
        if any(word in tokens for word in _WORD_RE.findall(kw.lower()))
    )
    denom = min(len(keywords), 3)
    return min(1.0, matched / denom)


def keyword_fit(item: Item, keywords: list[str]) -> float:
    """Rilevanza per keyword (0-1), esposta per filtrare gli item PRIMA dell'LLM.

    Il fit è già il moltiplicatore di rilevanza dello scoring; qui lo rendiamo
    pubblico per decidere, a costo zero (nessuna chiamata al modello), se vale
    la pena spendere il 7B su un item o se è del tutto fuori tema.
    """
    return _fit(item, keywords)


def _recency(item: Item) -> float:
    if item.created_at is None:
        return 0.4
    return _clamp(1 - _age_days(item) / 365.0)  # decadimento lineare su un anno


def score_item(
    item: Item,
    insight: IdeaInsight,
    config: AppConfig,
    observations: Sequence[ItemStat] | None = None,
) -> ScoreResult:
    """Punteggi di un item. ``observations`` è la storia engagement dell'item
    (``ItemStat`` in ordine qualsiasi): se assente, la heat usa l'euristica."""
    heat = _heat(item, config, observations)
    saturation = _saturation(item)

    credibility = _clamp(
        _SOURCE_CREDIBILITY.get(item.source, 0.30)
        + 0.30 * heat
        + (0.10 if item.author else 0.0)
    )

    feasibility = _DIFFICULTY_FEASIBILITY.get(insight.difficulty, 0.5)

    # Opportunità = è recente E non è già un mercato chiuso.
    opportunity = _clamp(0.5 * _recency(item) + 0.5 * (1.0 - saturation))

    fit = _fit(item, config.keywords)

    quality_values = {
        "heat": heat,
        "credibility": credibility,
        "feasibility": feasibility,
        "opportunity": opportunity,
    }
    weights = config.scoring.normalized_weights()
    q_weights = {m: weights.get(m, 0.0) for m in _QUALITY_METRICS}
    total_w = sum(q_weights.values()) or 1.0
    quality = sum(q_weights[m] * quality_values[m] for m in _QUALITY_METRICS) / total_w

    floor = config.scoring.relevance_floor
    relevance = floor + (1 - floor) * fit
    composite = _clamp(quality * relevance)

    status = (
        IdeaStatus.PROPOSED
        if composite >= config.scoring.threshold
        else IdeaStatus.PROCESSED
    )
    return ScoreResult(
        heat=heat,
        credibility=credibility,
        feasibility=feasibility,
        opportunity=opportunity,
        fit=fit,
        composite=composite,
        status=status,
    )
