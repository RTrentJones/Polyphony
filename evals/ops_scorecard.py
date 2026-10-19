"""Operational scorecard (W12-7, SPEC-87): six figures read from the DB.

    python -m evals.ops_scorecard --db <url> [--days 30] [--user <id>] [--rows <figure>]

A script for the one operator, not a product endpoint; `--rows` prints the SQL
behind one figure. The reason strings are the frozen contracts (tpm-plan-wave12
§1(b)) and 0.2 is RAG_SCORE_THRESHOLD: a grader does not import the rule it grades.
"""

import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

W = "j.created_at >= :since AND (CAST(:uid AS uuid) IS NULL OR j.user_id = CAST(:uid AS uuid))"
KEPT = f"{W} AND j.status = 'completed' AND j.deleted_at IS NULL"
SCENE_USAGE = (
    f"api_usage u JOIN jobs j ON j.id = u.job_id WHERE j.domain_type = 'scene' AND {W}"
)
FAILED = "coalesce(e->>'reason', '') IN ('no indexed voice samples', 'retrieval exhausted (200-row bound)')"
QUERIES = {
    "job_success": f"SELECT kind, count(*) FILTER (WHERE status = 'succeeded')::float / NULLIF(count(*) FILTER (WHERE status IN ('succeeded', 'dead')), 0) AS rate, count(*) FILTER (WHERE status = 'cancelled') AS cancelled FROM jobs j WHERE {W} GROUP BY kind ORDER BY kind",
    "queue_wait": f"SELECT percentile_disc(0.5) WITHIN GROUP (ORDER BY s) FILTER (WHERE ok) AS p50, percentile_disc(0.95) WITHIN GROUP (ORDER BY s) FILTER (WHERE ok) AS p95, count(*) FILTER (WHERE ok) AS n, count(*) FILTER (WHERE NOT ok) AS excluded FROM (SELECT EXTRACT(EPOCH FROM j.started_at - j.created_at)::float AS s, j.pause_count = 0 AND j.attempts = 1 AS ok FROM jobs j WHERE {W} AND j.started_at IS NOT NULL) q",
    "pause_duration": f"SELECT p.key AS reason, sum(p.value::float) AS total, percentile_disc(0.95) WITHIN GROUP (ORDER BY p.value::float) AS p95, count(*) AS jobs FROM jobs j, jsonb_each_text(j.pause_seconds) p WHERE {W} AND jsonb_typeof(j.pause_seconds) = 'object' GROUP BY p.key ORDER BY p.key",
    "grounding_failures": f"SELECT count(*) FILTER (WHERE bad)::float / NULLIF(count(*), 0) AS rate, count(*) FILTER (WHERE bad) AS failed, count(*) AS pairs FROM (SELECT bool_or({FAILED}) OR bool_and(coalesce((e->>'score')::float, 0) < 0.2) AS bad FROM scenes j, jsonb_each(j.evaluation_scores::jsonb->'grounding') b, jsonb_array_elements(b.value) e WHERE {W} AND coalesce(e->>'reason', '') <> 'context only' GROUP BY j.id, b.key, e->>'character_id') q",
    "cost_per_kept_scene": f"SELECT (SELECT sum(u.cost_usd) FROM {SCENE_USAGE})::float / NULLIF((SELECT count(*) FROM scenes j WHERE {KEPT}), 0) AS usd, (SELECT count(*) FROM scenes j WHERE {KEPT}) AS kept, (SELECT coalesce(sum(u.tokens_used), 0) FROM {SCENE_USAGE} AND u.cost_usd IS NULL) AS unpriced_tokens",
    "author_acceptance": f"SELECT count(*) FILTER (WHERE r.meta->>'status' = 'accepted')::float / NULLIF(count(*), 0) AS proposal_rate, count(*) AS proposals FROM scene_revisions r JOIN scenes j ON j.id = r.scene_id WHERE r.source = 'ai_revise' AND r.meta->>'status' IN ('accepted', 'discarded') AND {W}",
}


def edit_distance(a: str | None, b: str | None) -> float:
    """Word-level Levenshtein over the longer text's length."""
    x, y = (a or "").split(), (b or "").split()
    row = list(range(len(y) + 1))
    for i, w in enumerate(x, 1):
        prev, row[0] = row[0], i
        for j, u in enumerate(y, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (w != u))
    return row[-1] / max(len(x), len(y), 1)


async def scorecard(conn, days: int = 30, user: str | None = None) -> dict:
    p = {"since": datetime.now(timezone.utc) - timedelta(days=days), "uid": user}
    out = {}
    for name, sql in QUERIES.items():
        out[name] = [dict(r._mapping) for r in await conn.execute(text(sql), p)]
    kept = (
        await conn.execute(
            text(f"SELECT generated_content, content FROM scenes j WHERE {KEPT}"), p
        )
    ).all()
    out["author_acceptance"][0]["edited_at_least_once"] = sum(
        (c or "") != (g or "") for g, c in kept
    )
    out["author_acceptance"][0]["edit_distance"] = (
        sum(edit_distance(*s) for s in kept) / len(kept) if kept else None
    )
    return out


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--user")
    ap.add_argument("--rows", choices=sorted(QUERIES))
    a = ap.parse_args()
    if a.rows:
        return print(QUERIES[a.rows])
    engine = create_async_engine(a.db)
    async with engine.connect() as conn:
        print(json.dumps(await scorecard(conn, a.days, a.user), indent=2))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
