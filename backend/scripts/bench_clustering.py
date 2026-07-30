"""Misura il costo del clustering sul DB reale, PRIMA di decidere se vettorizzare.

Il piano (§2.1) propone numpy per `attach_item_to_idea`. Da allora è nato
l'IdeaIndex (pre-filtro sui centroidi in RAM): questo script dice quanto costa
davvero quello che resta, su dati veri e non su asintoti.

Uso:
    uv run python scripts/bench_clustering.py

Misura tre cose:
1. costruzione dell'indice (una volta per run);
2. `near()` in Python puro, per sonda e proiettato su un run tipico;
3. lo stesso `near()` con numpy, se installato (per decidere se `uv add numpy`
   vale il costo di una dipendenza in più).
"""

import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import select  # noqa: E402

from app.clustering import IdeaIndex  # noqa: E402
from app.db import get_session, init_db  # noqa: E402
from app.embeddings import unit  # noqa: E402
from app.models import Item  # noqa: E402

N_PROBES = 50
TYPICAL_RUN_ITEMS = 250  # item raccolti in un run reale (vedi Monitor)


def main() -> None:
    init_db()
    with get_session() as session:
        t0 = time.perf_counter()
        index = IdeaIndex(session)
        build_s = time.perf_counter() - t0

        embeddings = [
            item.embedding_json
            for item in session.exec(
                select(Item).where(Item.embedding_json.is_not(None)).limit(500)  # type: ignore[union-attr]
            ).all()
            if item.embedding_json
        ]

    if not embeddings or len(index) == 0:
        print("DB senza embedding o senza idee: lancia prima `idea-radar run`.")
        raise SystemExit(1)

    dim = len(embeddings[0])
    probes = [unit(e) for e in random.Random(42).sample(embeddings, min(N_PROBES, len(embeddings)))]
    print(f"idee nell'indice: {len(index)}, dimensione embedding: {dim}")
    print(f"costruzione indice: {build_s * 1000:.0f} ms (una volta per run)\n")

    # -- Python puro (il codice vero: index.near) -----------------------------
    times = []
    for probe in probes:
        t0 = time.perf_counter()
        index.near(probe, 0.71)  # threshold - margine, come nel run
        times.append(time.perf_counter() - t0)
    per_probe_ms = statistics.median(times) * 1000
    print(f"near() Python puro: {per_probe_ms:.1f} ms/item (mediana su {len(probes)} sonde)")
    print(f"  proiezione su un run da {TYPICAL_RUN_ITEMS} item: {per_probe_ms * TYPICAL_RUN_ITEMS / 1000:.1f} s")

    # -- numpy, se c'è ---------------------------------------------------------
    try:
        import numpy as np
    except ImportError:
        print("\nnumpy non installato: `uv add numpy` e rilancia per il confronto.")
        return

    matrix = np.array(list(index._units.values()), dtype=np.float32)
    ids = list(index._units.keys())
    np_times = []
    for probe in probes:
        v = np.asarray(probe, dtype=np.float32)
        t0 = time.perf_counter()
        sims = matrix @ v
        [ids[i] for i in np.flatnonzero(sims >= 0.71)]
        np_times.append(time.perf_counter() - t0)
    np_ms = statistics.median(np_times) * 1000
    print(f"\nnear() con numpy:   {np_ms:.2f} ms/item ({per_probe_ms / np_ms:.0f}x più veloce)")
    print(f"  proiezione su un run da {TYPICAL_RUN_ITEMS} item: {np_ms * TYPICAL_RUN_ITEMS / 1000:.2f} s")


if __name__ == "__main__":
    main()
