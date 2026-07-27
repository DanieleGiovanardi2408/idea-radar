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
from app.sources.profiles import profile_for

# Le metriche che si SOMMANO nella qualità. `fit` e `opportunity` non ci sono:
# sono moltiplicatori (vedi ``score_item``), non ingredienti di una media.
_QUALITY_METRICS = ("heat", "credibility", "feasibility")

# I parametri per-fonte (cap di velocità e saturazione, credibilità di base,
# live counter, riduzione dell'engagement) NON vivono più qui: ogni collector
# dichiara il proprio SourceProfile in app/sources/<fonte>.py e lo scoring lo
# legge con profile_for(item.source). Una fonte nuova non tocca questo modulo.

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
    # Il profilo su cui il `fit` è stato misurato: il macro-tema dell'idea.
    profile: str | None = None


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _gate(value: float, floor: float) -> float:
    """Moltiplicatore in ``[floor, 1]``: abbatte senza azzerare del tutto.

    Con ``floor=0`` un valore nullo cancella l'idea; con ``floor=1`` la metrica
    non conta più. Nel mezzo si sceglie quanto si è disposti a perdonare.
    """
    return floor + (1 - floor) * value


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
    """Riduzione scalare dell'engagement, secondo il profilo della fonte
    (pubblica: la usa anche la pipeline per fotografare l'engagement di ogni
    run in ``ItemStat``)."""
    return profile_for(item.source).engagement(item.engagement_json)


def _velocity(item: Item) -> float:
    """Stima *euristica* della velocità, quando la storia non c'è (cold start).

    Dove il profilo lo chiede (repo) dividiamo per l'età (stelle/giorno medie
    di vita). Su front page e feed no: sono freschi per costruzione, quindi
    l'engagement grezzo è già di fatto una misura di velocità.
    """
    absolute = absolute_engagement(item)
    if profile_for(item.source).velocity_per_age:
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
    profile = profile_for(item.source)
    cap = profile.velocity_cap
    if observations and profile.live_counter:
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
    profile = profile_for(item.source)
    popularity = _saturate(absolute_engagement(item), profile.saturation_cap)
    if not profile.maturity_in_saturation:
        return popularity
    # Un repo è "maturo" se è popolare *e* vecchio: 2 anni satura il fattore età.
    maturity = _clamp(_age_days(item) / 730.0)
    return _clamp(popularity * (0.4 + 0.6 * maturity))


def _fit(item: Item, keywords: list[str]) -> float:
    """Rilevanza per keyword con match su *parole intere* (no sottostringhe).

    Una keyword di più parole conta come matchata solo se compaiono TUTTE come
    token interi. Prima bastava una qualsiasi, e su una keyword come "home
    automation" significava "home" OPPURE "automation": qualunque articolo che
    parlasse di automazione prendeva punti come se fosse di domotica. Con i
    profili il difetto si vedeva subito — un profilo "domotica" che reclamava
    articoli di disaster recovery.

    Le parole intere restano il criterio (così 'ai' non matcha 'certain').
    """
    if not keywords:
        return 0.5
    haystack = f"{item.title} {item.text or ''}".lower()
    tokens = set(_WORD_RE.findall(haystack))
    matched = 0
    for keyword in keywords:
        words = _WORD_RE.findall(keyword.lower())
        if words and all(word in tokens for word in words):
            matched += 1
    # Due keyword matchate valgono "in pieno tema", una sola "lo sfiora".
    # Il denominatore era 3, tarato su un match lasco: con le frasi intere serviva
    # centrare tre keyword su cinque, cosa che quasi nessun item fa — sull'archivio
    # reale le idee sopra soglia crollavano da 55 a 3. E dentro un profilo le
    # keyword sono sinonimi dello stesso tema, quindi pretenderne tre è chiedere
    # che l'autore dell'articolo le usi tutte.
    denom = min(len(keywords), 2)
    return min(1.0, matched / denom)


def keyword_fit(item: Item, keywords: list[str]) -> float:
    """Rilevanza per keyword (0-1), esposta per filtrare gli item PRIMA dell'LLM.

    Il fit è già il moltiplicatore di rilevanza dello scoring; qui lo rendiamo
    pubblico per decidere, a costo zero (nessuna chiamata al modello), se vale
    la pena spendere il 7B su un item o se è del tutto fuori tema.
    """
    return _fit(item, keywords)


def profile_fits(item: Item, config: AppConfig) -> dict[str, float]:
    """Il fit dell'item per OGNI profilo configurato.

    Un unico fit medio su tutte le keyword del radar non dice niente: un'idea di
    domotica misurata anche su "prompt engineering" prende un voto mediocre che
    non distingue "fuori tema" da "a metà". Separando per profilo si ottiene la
    risposta giusta — centrale per uno, irrilevante per gli altri.
    """
    return {p.name: _fit(item, p.keywords) for p in config.effective_profiles()}


def best_profile(item: Item, config: AppConfig) -> tuple[str | None, float]:
    """(profilo, fit) del tema che rappresenta meglio l'item, o ``(None, 0.0)``.

    Il profilo è ``None`` quando NESSUN tema lo reclama. Ritornare comunque un
    nome sarebbe una bugia comoda: col ``max`` su tutti fit a zero vinceva sempre
    il primo profilo di ``config.yaml``, e sull'archivio reale 1371 idee su 1586
    finivano etichettate "ai-agents" senza avere niente a che fare con gli agenti.

    A parità di fit non nullo vince l'ordine di ``config.yaml``: i profili sono
    scritti a mano, quindi il primo è il più importante per chi li ha scritti.
    """
    fits = profile_fits(item, config)
    if not fits:
        return None, 0.0
    name, fit = max(fits.items(), key=lambda kv: kv[1])
    return (name, fit) if fit > 0 else (None, 0.0)


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
        profile_for(item.source).credibility_base
        + 0.30 * heat
        + (0.10 if item.author else 0.0)
    )

    feasibility = _DIFFICULTY_FEASIBILITY.get(insight.difficulty, 0.5)

    # Opportunità = è recente E non è già un mercato chiuso.
    opportunity = _clamp(0.5 * _recency(item) + 0.5 * (1.0 - saturation))

    # Il fit è quello del profilo che rappresenta meglio l'item: è la sua
    # rilevanza *nel tema giusto*, non una media su temi che non lo riguardano.
    profile, fit = best_profile(item, config)

    quality_values = {
        "heat": heat,
        "credibility": credibility,
        "feasibility": feasibility,
    }
    weights = config.scoring.normalized_weights()
    q_weights = {m: weights.get(m, 0.0) for m in _QUALITY_METRICS}
    total_w = sum(q_weights.values()) or 1.0
    quality = sum(q_weights[m] * quality_values[m] for m in _QUALITY_METRICS) / total_w

    # Due moltiplicatori, non addendi: sono le due condizioni SENZA le quali un
    # segnale non è un'opportunità, per quanto sia popolare o ben fatto.
    #
    # `fit`: se non è nel tuo tema, non è roba tua.
    # `opportunity`: se è già un mercato chiuso, non è un'apertura. Da addendo al
    #   30% non bastava — n8n, con opportunity 0.00 per saturazione piena,
    #   restava a 0.56 e quarto in classifica, cioè esattamente ciò che il
    #   README dice di non voler mostrare. Come moltiplicatore scende a 0.12.
    #
    # Il prezzo è che l'intera scala si comprime (il massimo sull'archivio reale
    # passa da 0.65 a 0.46): la soglia va tarata di conseguenza, non è più
    # confrontabile con quella di prima.
    relevance = _gate(fit, config.scoring.relevance_floor)
    openness = _gate(opportunity, config.scoring.opportunity_floor)
    composite = _clamp(quality * relevance * openness)

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
        profile=profile,
    )
