"""Profili di scoring per fonte: i numeri che prima vivevano hardcoded in scoring.py.

Ogni collector dichiara il proprio profilo accanto al codice che conosce la
fonte (cap di velocità, credibilità di base, se l'engagement è un contatore
vivo...). Lo scoring li legge dal registry: aggiungere una fonte nuova non
richiede più di toccare scoring.py — basta il modulo del collector, che si
registra da solo all'import.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceProfile:
    # Velocità (engagement/giorno) che vale heat = 1.0.
    velocity_cap: float = 100.0
    # Popolarità assoluta oltre la quale una cosa è "affermata" (satura).
    saturation_cap: float = 2_000.0
    # Credibilità di partenza della fonte (poi boost da heat e autore).
    credibility_base: float = 0.30
    # True se l'engagement è un CONTATORE VIVO (stelle, punti): lì un delta tra
    # osservazioni misura crescita reale. I feed che fotografano il valore alla
    # pubblicazione e non lo aggiornano mai restano sull'euristica.
    live_counter: bool = False
    # True se l'euristica di velocità divide per l'età (repo: stelle/giorno di
    # vita). Le front page, fresche per costruzione, usano l'engagement grezzo.
    velocity_per_age: bool = False
    # True se la saturazione pesa anche l'età: una cosa è "matura" se è
    # popolare E vecchia (il fattore età satura a 2 anni, vedi scoring).
    maturity_in_saturation: bool = False
    # Pesi per ridurre engagement_json a uno scalare (es. {"stars": 1, "forks": 2}).
    # None = somma di tutti i valori numerici.
    engagement_weights: dict[str, float] | None = None

    def engagement(self, engagement_json: dict | None) -> float:
        """Riduzione scalare dell'engagement grezzo, secondo il profilo.

        Le chiavi ``pypi_*`` sono dell'enricher (app/enrich.py), non della
        fonte: la somma cieca le salta, altrimenti migliaia di download
        finirebbero sommati ai 3 punti di un feed, gonfiando heat e
        saturazione. Lo scoring le legge come canale a parte.
        """
        e = engagement_json or {}
        if self.engagement_weights is None:
            return float(
                sum(
                    v
                    for k, v in e.items()
                    if isinstance(v, (int, float)) and not k.startswith("pypi_")
                )
            )
        return float(
            sum(
                weight * float(e.get(key) or 0)
                for key, weight in self.engagement_weights.items()
            )
        )


DEFAULT_PROFILE = SourceProfile()

_REGISTRY: dict[str, SourceProfile] = {}


def register_profile(source_name: str, profile: SourceProfile) -> None:
    """Registra il profilo di una fonte (chiamata dal modulo del collector)."""
    _REGISTRY[source_name] = profile


def profile_for(source_name: str) -> SourceProfile:
    """Profilo della fonte, o il default prudente per fonti sconosciute."""
    if not _REGISTRY:
        # I profili vivono nei moduli dei collector: importarli li registra.
        from app.sources.base import load_collectors

        load_collectors()
    return _REGISTRY.get(source_name, DEFAULT_PROFILE)
