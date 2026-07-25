"""Staged canon-grounded outline + fidelity gate (Phase 5)."""

import types

import pytest

from app.planning import staged_outline as so
from app.planning.canon import build_canon
from app.planning.fidelity import audit_outline

pytestmark = pytest.mark.unit


_PRINCIPALS = [
    {"name": "Milo Voss", "aliases": ["Milo", "Voss"]},
    {"name": "Zara Okafor", "aliases": ["Zara", "Okafor"]},
    {"name": "Edric Thane", "aliases": ["Edric", "Thane"]},
]
_KNOWN = {"Aeon Holdings", "Milo Voss", "Zara Okafor", "Edric Thane"}


class FakeLLM:
    """Returns canned text keyed by `purpose`; list values are consumed in order."""

    def __init__(self, responses: dict):
        self.responses = {
            k: list(v) if isinstance(v, list) else v for k, v in responses.items()
        }
        self.calls: list[str] = []

    async def generate(self, messages, *, purpose, **kw):
        self.calls.append(purpose)
        val = self.responses.get(purpose)
        text = val.pop(0) if isinstance(val, list) else val
        return types.SimpleNamespace(text=text, tokens_in=1, tokens_out=1)


class TestBuildCanon:
    async def test_assembles_and_derives_terms_and_principals(
        self, async_session, test_book
    ):
        from app.core.orm_models import CanonEntry, Character, StyleGuide

        test_book.synopsis = "A corporate afterlife."
        async_session.add_all(
            [
                Character(
                    book_id=test_book.id,
                    user_id=test_book.user_id,
                    name="Milo Voss",
                    role="protagonist",
                ),
                Character(
                    book_id=test_book.id,
                    user_id=test_book.user_id,
                    name="Edric Thane",
                    role="antagonist",
                ),
                CanonEntry(book_id=test_book.id, name="Aeon Holdings", category="org"),
                StyleGuide(book_id=test_book.id, pov="third-limited"),
            ]
        )
        await async_session.commit()

        canon = await build_canon(async_session, test_book.id)
        assert canon.title == test_book.title
        assert {c.name for c in canon.characters} == {"Milo Voss", "Edric Thane"}
        terms = canon.canon_terms()
        assert "Milo Voss" in terms and "Aeon Holdings" in terms
        assert "Milo" in terms  # name tokens are aliases
        principals = {p["name"] for p in canon.principals()}
        assert principals == {"Milo Voss", "Edric Thane"}  # both role-marked
        assert "third-limited" in canon.full_block()


class TestFidelityAudit:
    def test_elara_outline_fails_hard_gate(self):
        nodes = [{"title": "The Chosen One", "summary": "Elara defeats the Dark Lord."}]
        audit = audit_outline(nodes, _PRINCIPALS, _KNOWN)
        assert audit.passed is False
        assert audit.principal_recall == 0.0
        assert set(audit.missing) == {"Milo Voss", "Zara Okafor", "Edric Thane"}
        assert audit.warnings  # surfaces the miss

    def test_faithful_outline_passes(self):
        nodes = [
            {
                "title": "The Firm",
                "summary": "Milo Voss and Zara Okafor confront Edric Thane at Aeon Holdings.",
            }
        ]
        audit = audit_outline(nodes, _PRINCIPALS, _KNOWN)
        assert audit.passed is True
        assert audit.missing == []


class TestStagedOrchestration:
    def _canon(self):
        chars = [
            types.SimpleNamespace(
                name=n,
                role=r,
                description="",
                goals="",
                arc="",
                notes="",
                personality_traits=None,
                voice_characteristics=None,
                relationships=None,
            )
            for n, r in [
                ("Milo Voss", "protagonist"),
                ("Zara Okafor", "protagonist"),
                ("Edric Thane", "antagonist"),
            ]
        ]
        from app.planning.canon import Canon

        return Canon(title="Bored to Undeath", synopsis="x", characters=chars)

    async def test_stages_run_in_order_and_return_warnings(self, monkeypatch):
        faithful_beats = (
            '[{"title":"Ch1","summary":"Milo Voss and Zara Okafor face Edric Thane",'
            '"children":[{"title":"b","summary":"stuff","children":[]}]}]'
        )
        fake = FakeLLM(
            {
                "outline:skeleton": '{"premise_restated":"Milo Voss vs Edric Thane",'
                '"central_conflict":"labor","acts":[{"title":"A","goal":"g","turn":"t","chapters_planned":1}]}',
                "outline:chapters": '[{"title":"Ch1","summary":"s","act_index":0,'
                '"pov":"Milo Voss","characters":["Milo Voss"],"threads":[]}]',
                "outline:beats": faithful_beats,
            }
        )
        monkeypatch.setattr(so, "get_llm_client", lambda: fake)

        stages = []

        async def on_stage(s):
            stages.append(s)

        nodes, warnings = await so.generate_staged_outline(
            self._canon(), chapters_target=1, user_id=None, on_stage=on_stage
        )
        assert stages == ["skeleton", "chapters", "beats", "audit"]
        assert fake.calls[:2] == ["outline:skeleton", "outline:chapters"]
        assert nodes and nodes[0]["title"] == "Ch1"
        assert warnings == []  # faithful → no missing-principal warning

    async def test_missing_principal_triggers_one_repair(self, monkeypatch):
        missing = (
            '[{"title":"Ch1","summary":"Elara fights the Dark Lord",'
            '"children":[{"title":"b","summary":"x","children":[]}]}]'
        )
        faithful = (
            '[{"title":"Ch1","summary":"Milo Voss and Zara Okafor face Edric Thane",'
            '"children":[{"title":"b","summary":"x","children":[]}]}]'
        )
        fake = FakeLLM(
            {
                "outline:skeleton": '{"premise_restated":"p","central_conflict":"c",'
                '"acts":[{"title":"A","goal":"g","turn":"t","chapters_planned":1}]}',
                "outline:chapters": '[{"title":"Ch1","summary":"s","act_index":0,'
                '"pov":"","characters":[],"threads":[]}]',
                # first beats pass misses the cast; the repair pass is faithful.
                "outline:beats": [missing, faithful],
            }
        )
        monkeypatch.setattr(so, "get_llm_client", lambda: fake)

        nodes, warnings = await so.generate_staged_outline(
            self._canon(), chapters_target=1, user_id=None
        )
        # beats generated twice (original + one repair)
        assert fake.calls.count("outline:beats") == 2
        assert "Milo Voss" in nodes[0]["summary"]
        assert warnings == []  # repair recovered the cast


class TestGenerateEndpointEnqueues:
    async def test_outline_enqueues_staged_job(
        self, client, auth_headers, async_session, test_book
    ):
        from sqlalchemy import select

        from app.core.orm_models import Job

        test_book.synopsis = "A corporate afterlife satire."
        await async_session.commit()

        r = await client.post(
            f"/api/v1/books/{test_book.id}/plans/generate",
            json={"kind": "outline", "chapters_target": 6},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "generating"
        assert "plan_id" in body and "job_id" in body

        jobs = (
            (
                await async_session.execute(
                    select(Job).where(Job.kind == "generate_outline")
                )
            )
            .scalars()
            .all()
        )
        assert len(jobs) == 1
        assert jobs[0].payload["plan_id"] == body["plan_id"]
