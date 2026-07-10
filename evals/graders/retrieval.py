"""Retrieval grader: does the RAG store ground on the right character?

For each character, we index their gold *train* lines, then query with each
held-out *test* line and check whether the chunks the store returns actually
belong to that character. precision@k = fraction of returned chunks that are the
queried character's; MRR = mean reciprocal rank of the first correct chunk.

The grader is transport-agnostic: it takes a `retrieve(character_id, query, k)`
callable and a map of chunk-text -> owning character, so it can run against the
real store (via the API/embedder) or a synthetic in-memory index in unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RetrievalScore:
    precision_at_k: float
    mrr: float
    n_queries: int
    per_character: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "precision_at_k": round(self.precision_at_k, 4),
            "mrr": round(self.mrr, 4),
            "n_queries": self.n_queries,
            "per_character": {
                c: {"precision_at_k": round(v["p"], 4), "mrr": round(v["mrr"], 4)}
                for c, v in self.per_character.items()
            },
        }


def grade_retrieval(queries, retrieve, owner_of, k: int = 3) -> RetrievalScore:
    """
    queries:   list of (character, query_text) — the held-out test lines.
    retrieve:  callable(character, query_text, k) -> list[str] (returned chunk texts).
    owner_of:  callable(chunk_text) -> character name (gold owner of that chunk).
    """
    per: dict[str, dict] = {}
    tot_p, tot_rr, n = 0.0, 0.0, 0
    for character, q in queries:
        got = retrieve(character, q, k) or []
        hits = [owner_of(c) == character for c in got]
        p = sum(hits) / len(got) if got else 0.0
        rr = 0.0
        for i, h in enumerate(hits):
            if h:
                rr = 1.0 / (i + 1)
                break
        acc = per.setdefault(character, {"p": 0.0, "mrr": 0.0, "n": 0})
        acc["p"] += p
        acc["mrr"] += rr
        acc["n"] += 1
        tot_p += p
        tot_rr += rr
        n += 1
    for c, acc in per.items():
        acc["p"] /= acc["n"]
        acc["mrr"] /= acc["n"]
    return RetrievalScore(
        precision_at_k=tot_p / n if n else 0.0,
        mrr=tot_rr / n if n else 0.0,
        n_queries=n,
        per_character=per,
    )
