"""Insight LLM via Ollama (locale) con fallback euristico.

Due lezioni imparate sul campo, cablate qui dentro:

1. Una domanda generica ("perché è interessante?") produce risposte generiche e
   tutte uguali. Il prompt chiede quindi elementi *specifici e verificabili*:
   quale problema, per chi, e quale spazio resta scoperto.
2. Senza una rubrica esplicita il modello giudica "low" quasi tutto (52 su 54 in
   un run reale), rendendo la feasibility inutile. La difficulty ha ora ancore
   concrete per ciascun livello.
"""

import json
import logging
import re

import httpx
from pydantic import BaseModel

from app.config import Settings
from app.models import Difficulty, Item

logger = logging.getLogger(__name__)

_PROMPT = """Sei un analista che valuta opportunità tech per un piccolo team indipendente.

Analizza il contenuto e rispondi ESCLUSIVAMENTE con un oggetto JSON valido con queste chiavi:
{{"summary": "...", "why_text": "...", "difficulty": "low|med|high"}}

Regole per "summary": una frase che dica COSA fa concretamente questa cosa.
Vietato usare frasi passe-partout tipo "un progetto interessante" o "una soluzione innovativa".

Regole per "why_text": due frasi che dicano (a) quale problema specifico risolve e per chi,
(b) cosa resta scoperto o dove un piccolo team potrebbe inserirsi.
Sii concreto e specifico di QUESTO contenuto: se il tuo testo potrebbe valere per un
qualsiasi altro progetto, è sbagliato. Niente entusiasmo generico.

Rubrica OBBLIGATORIA per "difficulty" (quanto è difficile per un team di 1-3 persone
costruire qualcosa di competitivo in quest'area):
- "low": tooling o integrazione senza barriere; nessun dato proprietario; niente training di
  modelli; un prototipo utile in giorni. Esempi: uno script CLI, un wrapper di API, un plugin.
- "med": richiede infrastruttura non banale, un'app completa, o competenze di dominio;
  settimane o mesi. Esempi: un'app multiutente, un servizio con backend gestito.
- "high": richiede scala, dati proprietari, training di modelli, hardware, licenze,
  conformità normativa, o competere con incumbent finanziati. Esempi: un modello fondativo,
  una piattaforma cloud, un dispositivo fisico, un prodotto regolamentato.
Usa tutta la scala: la maggior parte delle cose NON è "low".

CONTENUTO
Titolo: {title}
Fonte: {source}
Autore: {author}
Segnali di engagement: {engagement}
Testo: {text}
"""

_TOPIC_PROMPT = """Ecco i titoli di idee tech che appartengono allo stesso gruppo:

{labels}

Rispondi ESCLUSIVAMENTE con un JSON: {{"label": "..."}}
dove "label" è un'etichetta di 2-5 parole che nomina il tema comune di questo gruppo.
Sii specifico (es. "agenti AI per il codice", non "tecnologia" o "vari").
Scrivi l'etichetta in ITALIANO, lasciando in inglese solo i termini tecnici che non
si traducono (es. "agenti AI per il self-hosting"). Non usare altri alfabeti.
"""


# Etichette ammesse: ASCII stampabile più le lettere accentate latine. Serve a
# scartare le risposte in altri alfabeti senza rifiutare "però" o "AI généré".
_LATIN_LABEL_RE = re.compile(r"^[\x20-\x7EÀ-ſ]+$")


def is_plausible_label(label: str) -> bool:
    """Se un'etichetta di topic è leggibile da chi usa questo radar.

    Serve in due punti: a valle della generazione, per rifiutare la risposta, e
    a monte, per accorgersi che un topic ne porta una già sbagliata da prima e
    valga la pena rinominarlo anche se la sua composizione non è cambiata.
    """
    return bool(label) and bool(_LATIN_LABEL_RE.match(label))


class IdeaInsight(BaseModel):
    summary: str
    why_text: str
    difficulty: Difficulty | None = None


class OllamaError(RuntimeError):
    """Ollama non raggiungibile o risposta non interpretabile."""


def _parse_difficulty(value: object) -> Difficulty | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    aliases = {"medium": "med", "high": "high", "low": "low", "med": "med"}
    normalized = aliases.get(normalized, normalized)
    try:
        return Difficulty(normalized)
    except ValueError:
        return None


def _item_context(item: Item) -> str:
    """Testo passato al modello: per i repo la sola description è troppo povera."""
    parts = [(item.text or "").strip()]
    raw = item.raw_json or {}
    if item.source == "github":
        topics = raw.get("topics") or []
        if topics:
            parts.append(f"Argomenti del repo: {', '.join(topics[:10])}")
        if raw.get("language"):
            parts.append(f"Linguaggio principale: {raw['language']}")
        if raw.get("stargazers_count") is not None:
            parts.append(f"Stelle: {raw['stargazers_count']}")
    return "\n".join(p for p in parts if p)[:2000] or "(nessun testo disponibile)"


class OllamaClient:
    """Client minimale per gli endpoint ``/api/generate`` di Ollama."""

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self._client = client
        self._owns_client = client is None

    def _generate_json(self, prompt: str) -> dict:
        client = self._client or httpx.Client(timeout=120.0)
        try:
            resp = client.post(
                f"{self.settings.ollama_host}/api/generate",
                json={
                    "model": self.settings.ollama_model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                },
            )
            resp.raise_for_status()
            return json.loads(resp.json()["response"])
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            raise OllamaError(str(exc)) from exc
        finally:
            if self._owns_client:
                client.close()

    def insight(self, item: Item) -> IdeaInsight:
        data = self._generate_json(
            _PROMPT.format(
                title=item.title,
                source=item.source,
                author=item.author or "(ignoto)",
                engagement=item.engagement_json or {},
                text=_item_context(item),
            )
        )
        return IdeaInsight(
            summary=str(data.get("summary") or item.title)[:500],
            why_text=str(data.get("why_text") or "")[:1000],
            difficulty=_parse_difficulty(data.get("difficulty")),
        )

    def topic_label(self, labels: list[str]) -> str:
        data = self._generate_json(
            _TOPIC_PROMPT.format(labels="\n".join(f"- {label}" for label in labels[:15]))
        )
        label = str(data.get("label") or "").strip()
        if not label:
            raise OllamaError("etichetta del topic vuota")
        if not _LATIN_LABEL_RE.match(label):
            # Il 7B a volte risponde in cinese ("AI开源与应用", "Open-source
            # macOS工具"): il prompt lo chiede in italiano, ma un prompt non è
            # una garanzia. Rifiutare fa tenere l'etichetta precedente, che è
            # sempre meglio di una in un alfabeto che non sai leggere.
            raise OllamaError(f"etichetta non in alfabeto latino: {label!r}")
        return label


def heuristic_insight(item: Item) -> IdeaInsight:
    """Insight senza LLM: usato come fallback quando Ollama non c'è."""
    base = (item.text or item.title or "").strip()
    summary = base[:200] if base else item.title
    return IdeaInsight(
        summary=summary,
        why_text=f"Segnale rilevato da {item.source}: {item.title}.",
        difficulty=None,
    )


def generate_insight(
    item: Item,
    settings: Settings,
    ollama: OllamaClient | None = None,
) -> IdeaInsight:
    """Prova l'LLM; se fallisce, applica la policy di ``llm_required``."""
    ollama = ollama or OllamaClient(settings)
    try:
        return ollama.insight(item)
    except OllamaError as exc:
        if settings.llm_required:
            raise
        logger.warning("Ollama non disponibile (%s): uso insight euristico.", exc)
        return heuristic_insight(item)
