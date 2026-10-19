"""W12-7: the scorecard's six figures on Postgres equal hand-worked constants."""

import asyncio
import os
import uuid

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.getenv("RUN_PG_TESTS"), reason="RUN_PG_TESTS unset"),
]
# s0 grounding: (b0,c1) has a 0.5; (b0,c2) no samples, (b1,c1) all < 0.2 fail; c3 is
# context only. s1: (b0,c2) retrieval exhausted fails. s2 is trashed, s3 failed.
SEED = """
INSERT INTO users (id, email, hashed_password, is_active) VALUES ('{u}', '{u}@ex.com', 'x', true);
INSERT INTO books (id, user_id, title, status) VALUES ('{b}', '{u}', 'B', 'drafting');
INSERT INTO scenes (id, user_id, book_id, position, status, generated_content, content, evaluation_scores, deleted_at) VALUES
 ('{s0}', '{u}', '{b}', 0, 'completed', 'a b c d', 'a b x d', '{{"grounding": {{"0": [{{"character_id": "c1", "score": 0.5}}, {{"character_id": "c1", "score": 0.1}}, {{"character_id": "c2", "score": null, "reason": "no indexed voice samples"}}], "1": [{{"character_id": "c1", "score": 0.1}}, {{"character_id": "c3", "reason": "context only"}}]}}}}', NULL),
 ('{s1}', '{u}', '{b}', 1, 'completed', 'one two', 'one two', '{{"grounding": {{"0": [{{"character_id": "c2", "reason": "retrieval exhausted (200-row bound)"}}]}}}}', NULL),
 ('{s2}', '{u}', '{b}', 2, 'completed', 't', 't', NULL, now()),
 ('{s3}', '{u}', '{b}', 3, 'failed', NULL, NULL, NULL, NULL);
INSERT INTO jobs (id, user_id, kind, payload, status, attempts, max_attempts, pause_count, pause_seconds, created_at, started_at, domain_type, domain_id) VALUES
 ('{j0}', '{u}', 'prose', '{{}}', 'succeeded', 1, 1, 0, NULL, now(), now() + interval '10 s', 'scene', '{s0}'),
 ('{j1}', '{u}', 'prose', '{{}}', 'dead', 1, 1, 0, NULL, now(), now() + interval '30 s', 'scene', '{s3}'),
 ('{j2}', '{u}', 'prose', '{{}}', 'succeeded', 1, 1, 2, '{{"quota exhausted": 60, "monthly_ceiling": 300}}', now(), now() + interval '100 s', 'scene', '{s1}'),
 ('{j3}', '{u}', 'check', '{{}}', 'cancelled', 0, 1, 0, NULL, now(), NULL, NULL, NULL),
 ('{j4}', '{u}', 'check', '{{}}', 'succeeded', 2, 2, 0, '{{"quota exhausted": 120}}', now(), now() + interval '50 s', NULL, NULL),
 ('{j5}', '{u}', 'prose', '{{}}', 'succeeded', 1, 1, 0, NULL, now() - interval '40 days', now(), NULL, NULL);
INSERT INTO api_usage (id, user_id, job_id, cost_usd, tokens_used) VALUES
 (gen_random_uuid(), '{u}', '{j0}', 0.30, 1000), (gen_random_uuid(), '{u}', '{j0}', NULL, 500), (gen_random_uuid(), '{u}', '{j2}', 0.10, 200),
 (gen_random_uuid(), '{u}', '{j1}', 0.20, 100), (gen_random_uuid(), '{u}', '{j4}', 5.0, 9);
INSERT INTO scene_revisions (id, scene_id, content, source, meta) VALUES
 (gen_random_uuid(), '{s0}', 'x', 'ai_revise', '{{"status": "accepted"}}'), (gen_random_uuid(), '{s0}', 'x', 'ai_revise', '{{"status": "discarded"}}'),
 (gen_random_uuid(), '{s0}', 'x', 'ai_revise', '{{"status": "accepted"}}'), (gen_random_uuid(), '{s0}', 'x', 'ai_revise', '{{"status": "proposed"}}'),
 (gen_random_uuid(), '{s0}', 'x', 'edited', '{{"status": "accepted"}}');
"""


async def test_the_six_figures_match_hand_computed_constants():
    from alembic import command
    from alembic.config import Config

    from app.core.database import get_async_db_url
    from evals.ops_scorecard import scorecard

    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    await asyncio.to_thread(command.upgrade, cfg, "head")
    ids = {k: uuid.uuid4() for k in "u b s0 s1 s2 s3 j0 j1 j2 j3 j4 j5".split()}
    engine = create_async_engine(get_async_db_url())
    async with engine.connect() as conn:
        raw = await conn.get_raw_connection()
        await raw.driver_connection.execute(SEED.format(**ids))
        got = await scorecard(conn, days=30, user=str(ids["u"]))
    await engine.dispose()
    v = lambda f: [tuple(r.values()) for r in got[f]]  # noqa: E731

    assert v("job_success") == [("check", 1.0, 1), ("prose", 2 / 3, 0)]
    # Clean (no pause, one attempt): 10 s and 30 s; the paused and retried jobs excluded.
    assert v("queue_wait") == [(10.0, 30.0, 2, 2)]
    assert v("pause_duration") == [
        ("monthly_ceiling", 300.0, 300.0, 1),
        ("quota exhausted", 180.0, 120.0, 2),
    ]
    assert v("grounding_failures") == [(0.75, 3, 4)]
    # (0.30 + 0.10 + 0.20) / 2 kept scenes; the check job is not a scene job.
    assert v("cost_per_kept_scene") == [(pytest.approx(0.3), 2, 500)]
    # 2 of 3 decided proposals; s0 edited ("c" -> "x": 1 of 4 words), s1 not.
    assert v("author_acceptance") == [(2 / 3, 3, 1, 0.125)]
