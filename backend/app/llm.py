"""Insight LLM via Ollama (locale) con fallback euristico.

Tre lezioni imparate sul campo, cablate qui dentro:

1. Una domanda generica ("perché è interessante?") produce risposte generiche e
   tutte uguali. Il prompt chiede quindi elementi *specifici e verificabili*:
   quale problema, per chi, e quale spazio resta scoperto.
2. Senza una rubrica esplicita il modello giudica "low" quasi tutto (52 su 54 in
   un run reale), rendendo la feasibility inutile. La difficulty ha ora ancore
   concrete per ciascun livello.
3. **Un divieto nel prompt non è una garanzia.** Le mosse passe-partout sono
   vietate da tre righe di prompt e continuavano ad arrivare — anzi, il 7B
   ricopiava alla lettera gli esempi del prompt ("scrivi la guida di
   riferimento", senza dire di cosa). Quello che il prompt chiede va quindi
   *verificato dopo*, sulla risposta: pattern per le mosse, similarità
   embedding per l'angolo. Stessa filosofia dell'etichetta in cinese —
   rifiutare, e semmai riprovare una volta sola.
"""

import json
import logging
import re
from collections.abc import Sequence

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

_MOVES_PROMPT = """Sei un operatore che sfrutta il vantaggio informativo: questa cosa sta salendo
ADESSO e quasi nessuno se n'è ancora accorto. Chi legge è un piccolo team indipendente (1-3 persone).

Rispondi ESCLUSIVAMENTE con un oggetto JSON valido: {{"moves": ["...", "...", "..."]}}

Regole per "moves": 2-3 azioni CONCRETE ed ESEGUIBILI QUESTA SETTIMANA per trarre vantaggio
dall'essere arrivati presto. Ogni mossa una frase, che inizi con un verbo all'imperativo.
Tipi di mossa validi: costruire qualcosa di piccolo che manca attorno a questa cosa,
occupare uno spazio (scrivere la guida di riferimento, il confronto, il benchmark che ancora
non esiste), integrarla dove dà un vantaggio, posizionarsi (dominio, canale, community) prima
che arrivi la folla.
Vietate le mosse passe-partout ("segui gli sviluppi", "approfondisci", "valuta l'uso"):
se la mossa vale per qualsiasi altra idea, è sbagliata. Aggancia ogni mossa a un dettaglio
specifico di QUESTO contenuto.

ESEMPI
✗ SBAGLIATA: "Scrivi la guida di riferimento." — è l'esempio qui sopra ricopiato: non dice
  la guida DI COSA né per chi, e varrebbe identica per qualsiasi altra idea del radar.
✗ SBAGLIATA: "Monitora gli sviluppi del progetto e valutane l'adozione." — non è un'azione,
  è un rinvio: chi legge sa già di dover guardare, vuole sapere cosa fare.
✓ GIUSTA: "Pubblica il confronto misurato con l'alternativa più citata nei commenti, sui due
  casi d'uso che il README dichiara non supportati." — si può fare questa settimana e sta in
  piedi solo per questo contenuto: nomina un dettaglio che altrove non esiste.

IDEA
Titolo: {label}
Cosa fa: {summary}
Perché conta: {why}
Segnali: {signals}
"""

# Coda aggiunta al prompt quando il primo giro è stato bocciato per intero. Dire
# *cosa* è stato rifiutato costa poco e serve: il modello che riceve solo "sei
# stato generico" ripropone la stessa mossa con altre parole.
_MOVES_RETRY = """
ATTENZIONE — il tuo tentativo precedente è stato RIFIUTATO. Queste mosse sono state scartate
perché passe-partout:
{rejected}
Riscrivile da capo: ognuna deve nominare un dettaglio che compare SOLO in questa idea
(un nome, un numero, un limite dichiarato, un'alternativa citata). Se non trovi il dettaglio,
scrivi meno mosse — una sola mossa concreta vale più di tre generiche.
"""

_ANGLE_PROMPT = """Sei un analista di opportunità per un piccolo team indipendente (1-3 persone).
Questa idea è tra le più calde del radar OGGI: il vantaggio è arrivare prima degli altri.

Rispondi ESCLUSIVAMENTE con un oggetto JSON valido: {{"angle": "..."}}

Regole per "angle": un mini-caso di business in 3-4 frasi, nell'ordine:
(1) CHI è il cliente con il problema (specifico: non "le aziende" ma chi, di preciso);
(2) COSA gli vendi o gli offri, costruibile da un team piccolo;
(3) PERCHÉ arrivare presto conta qui (cosa si chiude quando arriva la folla);
(4) il PRIMO passo verificabile in una settimana.
Sii concreto e specifico di QUESTA idea: se il testo potrebbe valere per un'altra, è sbagliato.
Scrivi in ITALIANO, termini tecnici in inglese dove serve.

ESEMPI
✗ SBAGLIATO: "Le agenzie di viaggio e i designer freelance potrebbero usare questa tecnologia
  per migliorare i loro processi e restare competitivi." — cliente inventato, offerta assente,
  e soprattutto non parla dell'idea: nessuna parola di questo testo viene da ciò che l'idea fa.
✓ GIUSTO: "Chi fa già girare questa cosa in locale e oggi perde mezza giornata a impacchettarla:
  gli vendi l'immagine pronta con la configurazione già tarata. Arrivare presto conta perché
  quando esce quella ufficiale il posto è occupato. Primo passo: pubblicarne una e misurare i pull."

IDEA
Titolo: {label}
Cosa fa: {summary}
Perché conta: {why}
Segnali: {signals}
"""

# Coda aggiunta quando l'angolo generato parlava d'altro (similarità sotto
# soglia). Gli si rimette davanti l'idea: il fallimento tipico è che il modello
# parte per la tangente al primo sostantivo che riconosce.
_ANGLE_RETRY = """
ATTENZIONE — il tuo tentativo precedente è stato RIFIUTATO perché parlava d'altro:
«{rejected}»
Quel testo non descriveva questa idea. Riscrivilo partendo da ciò che l'idea fa davvero
("Cosa fa" qui sopra): il cliente deve essere qualcuno che ha ESATTAMENTE quel problema.
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


# Le formule che rendono una mossa inutile: rinvii ("monitora", "approfondisci"),
# valutazioni al posto di azioni, e gli esempi del prompt ricopiati nudi. Sono i
# default; `moves.generic_patterns` in config.yaml li sostituisce, così chi ne
# incontra di nuove le aggiunge senza toccare il codice.
DEFAULT_GENERIC_MOVE_PATTERNS: tuple[str, ...] = (
    r"\b(segui|monitora|osserva|tieni d'occhio)\w*\s+(gli|lo|l'|il|la)?\s*"
    r"(svilupp|andamento|evoluzion|progress|aggiornament|novità|crescita)",
    r"\b(approfondisci|approfondire|informati|documentati)\b",
    r"\bvaluta\w*\s+(se|l'|la|il|di)?\s*(uso|utilizzo|adozione|impatto|possibilità|opportunità)",
    r"\brest(a|are)\s+aggiornat",
    r"\b(studia|esplora|analizza)\w*\s+(meglio|a fondo|il progetto|la tecnologia|le potenzialità)",
    r"\bconsidera\w*\s+(di\s+)?(usare|adottare|integrare)\b",
    r"\bsperimenta\w*\s+con\s+(la\s+tecnologia|il\s+progetto|lo\s+strumento)\b",
    # Gli esempi del prompt ricopiati senza complemento: "scrivi la guida di
    # riferimento." e basta, senza dire di cosa.
    r"^\W*(scrivi|crea|redigi|prepara)\s+(una|la)\s+guida(\s+di\s+riferimento)?\W*$",
    r"^\W*(fai|crea|prepara)\s+(un|il)\s+(benchmark|confronto)\W*$",
    r"\bdomina\w*\s+(il\s+canale|la\s+community|il\s+mercato)\b",
    r"^\W*posizionati\W*$",
)


class GenerationRejected(RuntimeError):
    """Il modello ha risposto, ma la risposta non supera la validazione.

    Distinta da :class:`OllamaError` di proposito, e la differenza conta per chi
    chiama: se Ollama è giù per un'idea è giù per tutte e tanto vale fermarsi,
    mentre una risposta generica riguarda *quell'* idea soltanto — le altre si
    fanno lo stesso.
    """


def generic_moves(moves: Sequence[str], patterns: Sequence[str]) -> list[str]:
    """Le mosse che matchano un pattern passe-partout (funzione pura, testabile).

    Un pattern non compilabile viene ignorato con un warning invece di far
    esplodere il run: arriva da config.yaml, cioè da un umano di fretta.
    """
    offenders = []
    for move in moves:
        for pattern in patterns:
            try:
                if re.search(pattern, move, re.IGNORECASE):
                    offenders.append(move)
                    break
            except re.error as exc:
                logger.warning("Pattern %r non valido, ignorato (%s).", pattern, exc)
    return offenders


def angle_similarity(angle: str, idea_text: str, embedder) -> float | None:
    """Quanto l'angolo di business parla davvero dell'idea. ``None`` = non giudicabile.

    Stesso modello di embedding del clustering e del filtro video, stessa regola
    di prudenza: se gli embedding non ci sono non si condanna nessuno, si tiene.
    """
    from app.embeddings import EmbeddingError, cosine, text_for_embedding

    try:
        vectors = embedder.embed_many(
            [text_for_embedding(idea_text), text_for_embedding(angle)]
        )
    except EmbeddingError as exc:
        logger.warning("Coerenza dell'angolo non verificabile (%s): lo tengo.", exc)
        return None
    if len(vectors) < 2 or not vectors[0] or not vectors[1]:
        return None
    return cosine(vectors[0], vectors[1])


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
        # Chiamate davvero fatte, rigenerazioni comprese. Il budget della fase
        # mosse legge questo contatore invece di contare le *idee*: da quando
        # una risposta bocciata ne costa due, le due cose non coincidono più e
        # un tetto che non se ne accorge non è un tetto.
        self.calls_made = 0

    def _generate_json(self, prompt: str, model: str | None = None) -> dict:
        self.calls_made += 1
        client = self._client or httpx.Client(timeout=120.0)
        try:
            resp = client.post(
                f"{self.settings.ollama_host}/api/generate",
                json={
                    "model": model or self.settings.ollama_model,
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
        # L'unica chiamata sul modello *insight*: è quella per-item (il collo di
        # bottiglia dei run), quindi l'unica dove un modello più piccolo paga.
        data = self._generate_json(
            _PROMPT.format(
                title=item.title,
                source=item.source,
                author=item.author or "(ignoto)",
                engagement=item.engagement_json or {},
                text=_item_context(item),
            ),
            model=self.settings.insight_model,
        )
        return IdeaInsight(
            summary=str(data.get("summary") or item.title)[:500],
            why_text=str(data.get("why_text") or "")[:1000],
            difficulty=_parse_difficulty(data.get("difficulty")),
        )

    def moves(
        self,
        label: str,
        summary: str,
        why: str,
        signals: str,
        *,
        generic_patterns: Sequence[str] = (),
    ) -> list[str]:
        """2-3 mosse concrete per sfruttare il vantaggio di sapere presto.

        Niente fallback euristico, di proposito: una mossa generica è peggio di
        nessuna mossa (lezione n.1 in cima al modulo). Se Ollama non c'è,
        l'idea resta senza e il run successivo ci riprova.

        Le passe-partout vengono *tolte*, non fatte rigenerare: se restano due
        mosse buone su tre, quelle due valgono già e una seconda chiamata
        costerebbe sette secondi per riscrivere ciò che va bene. Si rigenera
        solo quando non sopravvive niente — e una volta sola.
        """
        base = _MOVES_PROMPT.format(label=label, summary=summary, why=why, signals=signals)
        prompt = base
        for attempt in (1, 2):
            data = self._generate_json(prompt)
            raw = data.get("moves")
            if not isinstance(raw, list):
                raise OllamaError(f"'moves' non è una lista: {type(raw).__name__}")
            moves = [str(m).strip()[:300] for m in raw if str(m).strip()][:3]
            if not moves:
                raise OllamaError("nessuna mossa generata")
            offenders = generic_moves(moves, generic_patterns)
            if offenders:
                # Tutte le scartate nel log, come per il filtro video: i pattern
                # si tarano leggendo cosa hanno preso, non a memoria.
                logger.info(
                    "Mosse passe-partout scartate per «%s» (tentativo %d):\n  %s",
                    label[:48],
                    attempt,
                    "\n  ".join(repr(o) for o in offenders),
                )
            survivors = [m for m in moves if m not in offenders]
            if survivors:
                return survivors
            if attempt == 2:
                raise GenerationRejected(
                    f"solo mosse passe-partout dopo due tentativi: {offenders!r}"
                )
            prompt = base + _MOVES_RETRY.format(
                rejected="\n".join(f"- {o}" for o in offenders)
            )
        raise AssertionError("irraggiungibile")  # pragma: no cover

    def business_angle(
        self,
        label: str,
        summary: str,
        why: str,
        signals: str,
        *,
        embedder=None,
        min_similarity: float = 0.0,
    ) -> str:
        """Il mini-caso di business per un'idea in cima al radar.

        Con un ``embedder`` e una soglia, l'angolo generato viene confrontato
        con l'idea: sotto soglia sta parlando d'altro (il caso "agenzie di
        viaggio per un modello GGUF") e si riprova una volta sola. Senza
        embedder — o se gli embedding non rispondono — il testo passa: il
        controllo è una rete, non un cancello.
        """
        anchor = "\n".join(p for p in (label, summary, why) if p)
        base = _ANGLE_PROMPT.format(label=label, summary=summary, why=why, signals=signals)
        prompt = base
        for attempt in (1, 2):
            data = self._generate_json(prompt)
            angle = str(data.get("angle") or "").strip()[:1500]
            if not angle:
                raise OllamaError("angolo di business vuoto")
            if min_similarity <= 0 or embedder is None:
                return angle
            similarity = angle_similarity(angle, anchor, embedder)
            if similarity is None:
                return angle  # non giudicabile ≠ colpevole
            logger.info(
                "Angolo per «%s»: similarità %.2f (soglia %.2f)",
                label[:48],
                similarity,
                min_similarity,
            )
            if similarity >= min_similarity:
                return angle
            if attempt == 2:
                raise GenerationRejected(
                    f"angolo fuori tema anche al secondo tentativo "
                    f"(similarità {similarity:.2f} < {min_similarity:.2f})"
                )
            prompt = base + _ANGLE_RETRY.format(rejected=angle[:300])
        raise AssertionError("irraggiungibile")  # pragma: no cover

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
