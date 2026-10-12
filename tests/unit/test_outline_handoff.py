"""Outline -> chapter -> scene handoff (SPEC-10).

One test per acceptance criterion (AC4 is a frontend check), plus the pieces
they rest on: the structured node schema, the S2->S3 merge, the strict
structural cast in the fidelity audit, and promote-all.
"""

import types
import uuid

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.orm_models import BookPlan, Chapter, Character, Job
from app.jobs.worker import JobWorker
from app.planning import staged_outline as so
from app.planning.fidelity import audit_outline
from app.planning.outline import CastIndex, validate_outline_nodes

pytestmark = pytest.mark.unit


class _Ctx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *a):
        return False


def _bind(monkeypatch, session):
    import app.jobs.handlers as handlers_mod
    import app.jobs.repository as repo_mod
    import app.jobs.worker as worker_mod

    for mod in (worker_mod, handlers_mod, repo_mod):
        monkeypatch.setattr(mod, "get_async_session", lambda: _Ctx(session))


def _worker():
    from datetime import timedelta

    return JobWorker(
        poll_interval=0.01, stale_after=timedelta(minutes=30), worker_id="t"
    )


class FakeLLM:
    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[str] = []
        self.prompts: dict[str, list[str]] = {}

    async def generate(self, messages, *, purpose, **kw):
        self.calls.append(purpose)
        self.prompts.setdefault(purpose, []).append(messages[0]["content"])
        val = self.responses[purpose]
        text = val.pop(0) if isinstance(val, list) else val
        return types.SimpleNamespace(text=text, tokens_in=1, tokens_out=1)


_SKELETON = (
    '{"premise_restated":"Milo Voss, a dead intern, works for the CEL.",'
    '"central_conflict":"Milo and Zara against Edric Thane.",'
    '"acts":[{"title":"A","goal":"g","turn":"t","chapters_planned":2}]}'
)
_CHAPTERS = (
    '[{"title":"Onboarding","summary":"Milo arrives","act_index":0,'
    '"pov":"Milo","characters":["Milo Voss","Zara"],"threads":["the audit"]},'
    '{"title":"The Lich","summary":"Edric Thane rules","act_index":0,'
    '"pov":"the CEL","characters":["Edric Thane","Bob Nobody"],"threads":[]}]'
)
# The beat stage does NOT echo the structural fields: the handler's merge has to.
_BEATS = (
    '[{"title":"Onboarding","summary":"Milo Voss meets Zara Okafor",'
    '"children":[{"title":"Badge photo","summary":"Milo learns he is dead",'
    '"children":[]}]},'
    '{"title":"The Lich","summary":"Edric Thane decrees overtime",'
    '"children":[{"title":"Memo","summary":"a decree","children":[]}]}]'
)


async def _cast(session, book):
    rows = [
        Character(
            book_id=book.id, user_id=book.user_id, name="Milo Voss", role="protagonist"
        ),
        Character(
            book_id=book.id,
            user_id=book.user_id,
            name="Zara Okafor",
            role="protagonist",
        ),
        Character(
            book_id=book.id,
            user_id=book.user_id,
            name="Edric Thane",
            role="antagonist",
            aliases=["the CEL"],
        ),
    ]
    session.add_all(rows)
    book.synopsis = "A corporate afterlife satire. The CEL runs Aeon Holdings."
    await session.commit()
    return {c.name: str(c.id) for c in rows}


# --- AC1: the premise checkpoint ---------------------------------------------------


async def test_ac1_premise_checkpoint_stops_then_resumes_with_the_edit(
    client, auth_headers, async_session, test_book, monkeypatch
):
    _bind(monkeypatch, async_session)
    monkeypatch.setattr(settings, "LLM_TIER", "free")
    ids = await _cast(async_session, test_book)
    fake = FakeLLM(
        {
            "outline:skeleton": _SKELETON,
            "outline:chapters": _CHAPTERS,
            "outline:beats": _BEATS,
        }
    )
    monkeypatch.setattr(so, "get_llm_client", lambda: fake)
    bid = test_book.id

    # Free tier: confirm_premise defaults on.
    r = await client.post(
        f"/api/v1/books/{bid}/plans/generate",
        json={"kind": "outline", "chapters_target": 3},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    plan_id = r.json()["plan_id"]
    assert await _worker().run_once() is True

    # One LLM call, the job ended cleanly, the plan waits with the premise stored.
    assert fake.calls == ["outline:skeleton"]
    job = (await async_session.execute(select(Job))).scalars().one()
    assert job.status == "succeeded"
    plans = (
        await client.get(f"/api/v1/books/{bid}/plans", headers=auth_headers)
    ).json()["plans"]
    assert plans[0]["status"] == "awaiting_confirmation"
    assert plans[0]["premise"]["premise_restated"].startswith("Milo Voss")
    assert plans[0]["premise"]["central_conflict"]
    assert plans[0]["content"] == []

    edited = "Milo Voss is dead; the CEL is the Chief Executive Lich, Edric Thane."
    r = await client.post(
        f"/api/v1/books/{bid}/plans/{plan_id}/confirm-premise",
        json={"premise_restated": edited},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "generating"
    assert await _worker().run_once() is True

    # Resumed from the stored skeleton: S1 is not paid for twice, and the
    # author's edit is what the chapters stage was given, as authoritative.
    assert fake.calls.count("outline:skeleton") == 1
    chapters_prompt = fake.prompts["outline:chapters"][0]
    assert edited in chapters_prompt
    assert "confirmed by the author" in chapters_prompt

    plan = (await async_session.execute(select(BookPlan))).scalars().one()
    await async_session.refresh(plan)
    assert plan.status == "ready"
    assert plan.premise["premise_restated"] == edited
    assert plan.premise["confirmed"] is True and plan.premise["edited"] is True
    first, second = plan.content
    # S2's structural fields were merged onto S3's nodes and resolved to ids.
    assert first["pov"] == "Milo Voss"
    assert first["character_ids"] == [ids["Milo Voss"], ids["Zara Okafor"]]
    assert first["act_index"] == 0 and first["threads"] == ["the audit"]
    assert second["pov"] == "Edric Thane"  # "the CEL" resolved through the alias
    assert second["character_ids"] == [ids["Edric Thane"]]  # Bob dropped ...
    assert any("Bob Nobody" in w for w in plan.warnings)  # ... and reported
    assert uuid.UUID(first["node_id"]) and uuid.UUID(first["children"][0]["node_id"])


async def test_confirm_premise_only_when_waiting(
    client, auth_headers, async_session, test_book
):
    plan = BookPlan(book_id=test_book.id, kind="outline", content=[], status="ready")
    async_session.add(plan)
    await async_session.commit()
    r = await client.post(
        f"/api/v1/books/{test_book.id}/plans/{plan.id}/confirm-premise",
        json={},
        headers=auth_headers,
    )
    assert r.status_code == 409


async def test_paid_tier_runs_straight_through(
    client, auth_headers, async_session, test_book, monkeypatch
):
    monkeypatch.setattr(settings, "LLM_TIER", "paid")
    r = await client.post(
        f"/api/v1/books/{test_book.id}/plans/generate",
        json={"kind": "outline"},
        headers=auth_headers,
    )
    job = (await async_session.execute(select(Job))).scalars().one()
    assert r.status_code == 200 and job.payload["confirm_premise"] is False


async def test_cancelled_continuation_goes_back_to_waiting(async_session, test_book):
    from app.jobs.handlers import _cancel_plan

    import app.jobs.handlers as handlers_mod

    plan = BookPlan(
        book_id=test_book.id,
        kind="outline",
        content=[],
        status="generating",
        premise={"premise_restated": "p", "central_conflict": "c", "acts": []},
    )
    async_session.add(plan)
    await async_session.commit()
    real = handlers_mod.get_async_session
    handlers_mod.get_async_session = lambda: _Ctx(async_session)
    try:
        await _cancel_plan(
            {"plan_id": str(plan.id), "prior_status": "awaiting_confirmation"}
        )
    finally:
        handlers_mod.get_async_session = real
    await async_session.refresh(plan)
    assert plan.status == "awaiting_confirmation"


# --- AC2: structured nodes round-trip, through a restore -------------------------


def _people():
    return [
        types.SimpleNamespace(id=uuid.uuid4(), name="Milo Voss", aliases=[]),
        types.SimpleNamespace(id=uuid.uuid4(), name="Edric Thane", aliases=["the CEL"]),
    ]


def test_validator_resolves_names_through_aliases_and_assigns_ids():
    milo, edric = _people()
    nodes = validate_outline_nodes(
        [
            {
                "title": "Ch1",
                "pov": "the CEL",
                "characters": ["Milo", "Ghost"],
                "act_index": 2,
                "threads": ["audit", ""],
                "children": [{"title": "beat"}],
            }
        ],
        cast=[milo, edric],
    )
    node = nodes[0]
    assert node["pov"] == "Edric Thane"
    assert node["character_ids"] == [str(edric.id), str(milo.id)]  # pov first
    assert node["act_index"] == 2 and node["threads"] == ["audit"]
    assert uuid.UUID(node["node_id"]) and uuid.UUID(node["children"][0]["node_id"])
    # Re-validation (an edit, a version, a restore) keeps everything, ids too.
    assert validate_outline_nodes(nodes) == nodes
    assert validate_outline_nodes(nodes, cast=[milo, edric]) == nodes
    with pytest.raises(ValueError, match="Ghost"):
        validate_outline_nodes(
            [{"title": "x", "characters": ["Ghost"]}], cast=[milo], strict=True
        )


async def test_ac2_structured_outline_round_trips_through_a_restore(
    client, auth_headers, async_session, test_book
):
    ids = await _cast(async_session, test_book)
    bid = str(test_book.id)
    outline = [
        {
            "title": "Onboarding",
            "summary": "s",
            "pov": "Milo Voss",
            "character_ids": [ids["Milo Voss"], "Zara"],
            "act_index": 1,
            "children": [{"title": "beat", "summary": ""}],
        }
    ]
    r = await client.put(
        f"/api/v1/books/{bid}/plans",
        json={"kind": "outline", "content": outline},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    v1 = r.json()["content"]
    assert v1[0]["character_ids"] == [ids["Milo Voss"], ids["Zara Okafor"]]
    plan_id = r.json()["id"]

    # An author edit naming someone outside the cast is rejected, not dropped.
    bad = await client.put(
        f"/api/v1/books/{bid}/plans",
        json={"kind": "outline", "content": [{"title": "x", "pov": "Elara"}]},
        headers=auth_headers,
    )
    assert bad.status_code == 400 and "Elara" in bad.text

    await client.put(
        f"/api/v1/books/{bid}/plans",
        json={"kind": "outline", "content": [{"title": "Replaced"}]},
        headers=auth_headers,
    )
    rr = await client.post(
        f"/api/v1/books/{bid}/versions/book_plan/{plan_id}/restore/1",
        headers=auth_headers,
    )
    assert rr.status_code == 200, rr.text
    restored = (
        await client.get(f"/api/v1/books/{bid}/plans", headers=auth_headers)
    ).json()["plans"][0]["content"]
    assert restored == v1  # pov, character_ids, act_index and node_ids kept


# --- AC3: promote is idempotent and carries the cast --------------------------------


async def _structured_plan(client, auth_headers, test_book, ids):
    outline = [
        {
            "title": "Onboarding",
            "summary": "Milo arrives",
            "pov": "Milo Voss",
            "character_ids": [ids["Milo Voss"], ids["Zara Okafor"]],
            "children": [{"title": "Badge photo", "summary": "he is dead"}],
        },
        {"title": "The Lich", "summary": "decree", "pov": "Edric Thane"},
    ]
    r = await client.put(
        f"/api/v1/books/{test_book.id}/plans",
        json={"kind": "outline", "content": outline},
        headers=auth_headers,
    )
    return r.json()["content"]


async def test_ac3_promoting_the_same_node_twice_yields_one_chapter(
    client, auth_headers, async_session, test_book
):
    ids = await _cast(async_session, test_book)
    nodes = await _structured_plan(client, auth_headers, test_book, ids)
    url = f"/api/v1/books/{test_book.id}/plans/promote"

    first = await client.post(
        url, json={"node_id": nodes[0]["node_id"]}, headers=auth_headers
    )
    again = await client.post(url, json={"node_index": 0}, headers=auth_headers)
    assert first.status_code == 200 and again.status_code == 200
    assert first.json()["created"] is True and again.json()["created"] is False
    assert first.json()["chapter_id"] == again.json()["chapter_id"]

    chapters = (
        (
            await async_session.execute(
                select(Chapter).where(Chapter.book_id == test_book.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(chapters) == 1
    ch = chapters[0]
    assert str(ch.plan_node_id) == nodes[0]["node_id"]
    assert str(ch.pov_character_id) == ids["Milo Voss"]
    assert ch.character_ids == [ids["Milo Voss"], ids["Zara Okafor"]]
    assert "Badge photo" in ch.summary

    detail = (
        await client.get(f"/api/v1/books/{test_book.id}", headers=auth_headers)
    ).json()
    assert detail["chapters"][0]["character_ids"] == ch.character_ids
    assert detail["chapters"][0]["plan_node_id"] == nodes[0]["node_id"]


async def test_promote_all_is_idempotent(
    client, auth_headers, async_session, test_book
):
    ids = await _cast(async_session, test_book)
    nodes = await _structured_plan(client, auth_headers, test_book, ids)
    url = f"/api/v1/books/{test_book.id}/plans/promote-all"
    first = (await client.post(url, headers=auth_headers)).json()
    again = (await client.post(url, headers=auth_headers)).json()
    assert first["created"] == 2 and again["created"] == 0
    assert [c["plan_node_id"] for c in again["chapters"]] == [
        n["node_id"] for n in nodes
    ]
    assert [c["position"] for c in first["chapters"]] == [0, 1]


async def test_promote_unknown_node_id_is_404(
    client, auth_headers, async_session, test_book
):
    ids = await _cast(async_session, test_book)
    await _structured_plan(client, auth_headers, test_book, ids)
    r = await client.post(
        f"/api/v1/books/{test_book.id}/plans/promote",
        json={"node_id": str(uuid.uuid4())},
        headers=auth_headers,
    )
    assert r.status_code == 404
    r = await client.post(
        f"/api/v1/books/{test_book.id}/plans/promote", json={}, headers=auth_headers
    )
    assert r.status_code == 422


# --- the pieces ------------------------------------------------------------------


def test_merge_fills_only_what_the_beat_stage_left_out():
    chapters = [{"pov": "Milo", "characters": ["Milo"], "act_index": 1}]
    nodes = [{"title": "t", "pov": "Zara", "characters": []}]
    merged = so.merge_structural(chapters, nodes)
    assert merged[0]["pov"] == "Zara"  # the echo wins
    assert merged[0]["characters"] == ["Milo"] and merged[0]["act_index"] == 1


def test_audit_counts_character_ids_as_presence_and_flags_strangers():
    milo, edric = _people()
    cast = CastIndex([milo, edric])
    principals = [
        {"name": "Milo Voss", "aliases": ["Milo"]},
        {"name": "Edric Thane", "aliases": ["the CEL"]},
    ]
    nodes = [
        {
            "title": "Ch1",
            "summary": "nobody is named in prose",
            "character_ids": [str(milo.id), str(edric.id)],
        },
        {"title": "Ch2", "summary": "x", "pov": "Elara"},
    ]
    audit = audit_outline(nodes, principals, {"Milo Voss", "Edric Thane"}, cast=cast)
    assert audit.passed  # structural cast counts toward the hard gate
    assert any("Ch2: Elara" in w for w in audit.warnings)
