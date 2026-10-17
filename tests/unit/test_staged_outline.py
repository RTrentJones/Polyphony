"""Staged canon-grounded outline + fidelity gate (Phase 5)."""

import types

import pytest

from app.planning import staged_outline as so
from app.planning.canon import build_canon
from app.planning.fidelity import audit_outline

pytestmark = pytest.mark.unit
# (F) W8-5 (D9): the one outline POV line; it replaces W6-4's scene-only sentence.
D9 = "Write each summary as what the chapter's POV character witnesses or learns; never what they fail to notice or will later learn."
OLD_D9 = "Write each scene summary as what the chapter's POV character witnesses;"


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
        self.formats: set[str] = set()
        self.prompts: dict[str, list[str]] = {}

    async def generate(self, messages, *, purpose, **kw):
        self.calls.append(purpose)
        self.formats.add(str(kw.get("response_format")))
        self.prompts.setdefault(purpose, []).append(messages[0]["content"])
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
                '"pov":"Milo Voss","people":["Milo Voss"],"threads":[]}]',
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
        # The CHAPTER stage is where the failure originates and must be repaired:
        # first chapter result omits the cast, the repair result includes them.
        cast_empty_chapters = (
            '[{"title":"Ch1","summary":"a chapter","act_index":0,'
            '"pov":"","people":[],"threads":[]}]'
        )
        repaired_chapters = (
            '[{"title":"Ch1","summary":"Milo Voss and Zara Okafor vs Edric Thane",'
            '"act_index":0,"pov":"Milo Voss",'
            '"people":["Milo Voss","Zara Okafor","Edric Thane"],"threads":[]}]'
        )
        missing_beats = (
            '[{"title":"Ch1","summary":"nobody named",'
            '"children":[{"title":"b","summary":"x","children":[]}]}]'
        )
        faithful_beats = (
            '[{"title":"Ch1","summary":"Milo Voss and Zara Okafor face Edric Thane",'
            '"children":[{"title":"b","summary":"x","children":[]}]}]'
        )
        fake = FakeLLM(
            {
                "outline:skeleton": '{"premise_restated":"p","central_conflict":"c",'
                '"acts":[{"title":"A","goal":"g","turn":"t","chapters_planned":1}]}',
                # first chapter pass omits the cast; the repair pass includes them.
                "outline:chapters": [cast_empty_chapters, repaired_chapters],
                "outline:beats": [missing_beats, faithful_beats],
            }
        )
        monkeypatch.setattr(so, "get_llm_client", lambda: fake)

        nodes, warnings = await so.generate_staged_outline(
            self._canon(), chapters_target=1, user_id=None
        )
        # Repair regenerates CHAPTERS (where cast is chosen) AND beats (PR review
        # #5) — both stages run twice, and the SECOND chapter request carries the
        # missing principals as explicit feedback.
        assert fake.calls.count("outline:chapters") == 2
        assert fake.calls.count("outline:beats") == 2
        repair_prompt = fake.prompts["outline:chapters"][1]
        assert "Milo Voss" in repair_prompt and "Edric Thane" in repair_prompt
        assert "OMITTED" in repair_prompt  # the repair-feedback marker
        for prompt in fake.prompts["outline:chapters"]:  # (F) D9, repair too
            assert prompt.count(D9) == 1
        assert "Milo Voss" in nodes[0]["summary"]
        assert warnings == []  # repair recovered the cast


class TestBeatsStageSeesThePlan:
    """SPEC-18 (B11): the beats stage gets the premise, the central conflict
    and its act's goal and turn; acts are numbered from 1; embedded structures
    are JSON."""

    _SKELETON = (
        '{"premise_restated":"Milo Voss, a dead accountant, organises the '
        "afterlife's clerks against the Chief Executive Lich.\","
        '"central_conflict":"Milo\'s union against Edric Thane\'s quotas",'
        '"acts":[{"title":"Onboarding","goal":"Milo learns the rules",'
        '"turn":"Milo refuses a soul-quota"},'
        '{"title":"The Strike","goal":"the clerks walk out",'
        '"turn":"Edric concedes the ledger"}]}'
    )
    _CHAPTERS = (
        '[{"title":"Ch1","summary":"Milo Voss signs on","act_index":0,'
        '"pov":"Milo Voss","people":["Milo Voss","Zara Okafor"],"threads":[]},'
        '{"title":"Ch2","summary":"Edric Thane yields","act_index":1,'
        '"pov":"Zara Okafor","people":["Edric Thane","Zara Okafor"],"threads":[]}]'
    )
    _BEATS = [
        '[{"title":"Ch1","summary":"Milo Voss and Zara Okafor",'
        '"children":[{"title":"b","summary":"x","children":[]}]}]',
        '[{"title":"Ch2","summary":"Edric Thane gives way",'
        '"children":[{"title":"b","summary":"x","children":[]}]}]',
    ]

    async def _run(self, monkeypatch, **kw):
        fake = FakeLLM(
            {
                "outline:skeleton": self._SKELETON,
                "outline:chapters": self._CHAPTERS,
                "outline:beats": list(self._BEATS),
            }
        )
        monkeypatch.setattr(so, "get_llm_client", lambda: fake)
        await so.generate_staged_outline(
            TestStagedOrchestration()._canon(), chapters_target=2, **kw
        )
        return fake

    async def test_beats_prompt_carries_premise_conflict_and_act_plan(
        self, monkeypatch
    ):
        fake = await self._run(monkeypatch)
        act1, act2 = fake.prompts["outline:beats"]
        for prompt in (act1, act2):
            assert "organises the afterlife's clerks" in prompt
            assert "Milo's union against Edric Thane's quotas" in prompt
            assert "authoritative" not in prompt  # not confirmed by the author
            assert D9 in prompt and prompt.count(D9) == 1
            assert OLD_D9 not in prompt
            assert "Do not reuse a chapter title as a scene title." in prompt
        assert "This is act 1 of 2: Onboarding" in act1
        assert "Act 1 goal: Milo learns the rules" in act1
        assert "Milo refuses a soul-quota" in act1
        assert "This is act 2 of 2: The Strike" in act2
        assert "Edric concedes the ledger" in act2
        assert "Edric concedes" not in act1  # each act sees its own turn
        # stub markers (evals/stub_llm.py) survive
        assert "scene beats" in act1 and "mirroring the chapters" in act1

    async def test_the_chapters_stage_prints_the_outline_pov_line(self, monkeypatch):
        """(F) SPEC-63 (D9): one line, after the cast rules, before the JSON
        ask; written against W7br ch 3, whose summary says what Milo cannot
        witness ("in the corner office the Lich notices a query")."""
        fake = await self._run(monkeypatch)
        (chapters,) = fake.prompts["outline:chapters"]
        assert so.OUTLINE_POV_LINE == D9 and chapters.count(D9) == 1
        assert OLD_D9 not in chapters
        at = chapters.index
        assert at("Break the acts into chapters.") < at(D9) < at("Return ONLY")

    async def test_acts_are_numbered_from_one(self, monkeypatch):
        fake = await self._run(monkeypatch)
        act1, act2 = fake.prompts["outline:beats"]
        assert "chapters of act 1, in order" in act1
        assert "chapters of act 2, in order" in act2
        for prompt in fake.prompts["outline:beats"]:
            assert "act 0" not in prompt.lower()
        chapters = fake.prompts["outline:chapters"][0]
        assert '"act": 1' in chapters and '"act_index": 0' in chapters

    async def test_confirmed_premise_is_authoritative_in_the_beats_stage(
        self, monkeypatch
    ):
        import json

        skeleton = json.loads(self._SKELETON)
        skeleton["premise_restated"] = "The author's own rewritten premise."
        skeleton["confirmed"] = True
        fake = await self._run(monkeypatch, skeleton=skeleton)
        assert "outline:skeleton" not in fake.calls
        for prompt in fake.prompts["outline:beats"]:
            assert (
                "Premise (confirmed by the author: treat it as authoritative): "
                "The author's own rewritten premise." in prompt
            )

    async def test_no_python_repr_in_any_staged_prompt(self, monkeypatch):
        fake = await self._run(monkeypatch)
        prompts = [p for ps in fake.prompts.values() for p in ps]
        assert len(prompts) == 4  # skeleton, chapters, two acts of beats
        for prompt in prompts:
            assert "{'" not in prompt and "['" not in prompt
        # the chapters reach the beats stage as parseable JSON
        act1 = fake.prompts["outline:beats"][0]
        body = act1.split("chapters of act 1, in order:\n")[1].split("\n\nFor EACH")[0]
        import json

        assert json.loads(body)[0]["title"] == "Ch1"

    async def test_each_act_sees_the_whole_book(self, monkeypatch):
        """(F) W4-5: every chapter, this act's marked (its summary is in the act
        JSON below), before the act header; later acts see earlier beats."""
        from app.core.prompt_markers import OUTLINE_CHAPTERS_HEADER as HEADER

        # (R) the moved marker formats to wave 4's text, byte for byte.
        EARLIER = "Beats already planned in earlier acts:"
        fake = await self._run(monkeypatch)
        act1, act2 = fake.prompts["outline:beats"]
        for prompt, lines in (
            (act1, ["→ 1. Ch1", "  2. Ch2: Edric Thane yields"]),
            (act2, ["  1. Ch1: Milo Voss signs on", "→ 2. Ch2", EARLIER]),
        ):
            block = prompt.split(f"{HEADER}\n")[1].split("\n\n")[0].splitlines()
            assert block[: len(lines)] == lines
            assert block[-1] == so.INTRODUCE_NO_EARLIER
            assert ("- Ch1: b" in block) is (prompt is act2)
            assert prompt.index(HEADER) < prompt.index("in order:")

    def test_the_whole_book_costs_at_most_2500_characters_an_act(self):
        """(F) Cost guard: cel at 6 chapters, at the run-W3 outline's sizes
        (~400-character summaries, four beats a chapter)."""
        summary = ("Milo signs the form and learns what the job costs him. " * 8)[:400]
        outline = [
            {"title": f"Chapter {i}: The Desk", "summary": summary, "act_index": i // 2}
            for i in range(6)
        ]
        beats = [{"title": f"The beat named {j}"} for j in range(4)]
        for act in range(3):
            earlier = [{**c, "children": beats} for c in outline[: 2 * act]]
            mine = outline[2 * act : 2 * act + 2]
            assert len(so.outline_context(outline, mine, earlier)) <= 2500

    def test_earlier_beats_with_null_or_numeric_titles_still_join(self):
        """A checkpointed act is raw model output, validated only at the end."""
        earlier = [
            {
                "title": "Ch1",
                "children": [{"title": None}, {"title": 7}, {"title": "b"}],
            }
        ]
        assert "- Ch1: ; 7; b" in so.outline_context([], [], earlier)


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


def test_a_chapter_lists_everyone_its_scenes_name():
    """(F) W6-4 (c): W5b's "Signature Sheet" gains Edric; (R) W4b2's "Retained"."""
    sheet = ["Milo Voss", "Zara Okafor", "Prava", "Old Fen", "Halvard Bell"]
    kept = ["Milo Voss", "Prava", "Zara Okafor"]
    nodes = [{"people": sheet, "children": [{"people": ["Edric Thane"]}]}]
    nodes.append({"people": kept, "children": [{"people": ["Prava"]}]})
    merged = [n["people"] for n in so.merge_structural([], nodes)]
    assert merged == [[*sheet, "Edric Thane"], kept]


async def test_each_outline_and_plan_call_asks_the_provider_for_json(monkeypatch):
    """(F) W6-6 (a), D11: the four calls send JSON_OBJECT; arrays are {"items"}."""
    from app.llm.json_utils import JSON_OBJECT
    from app.orchestration import workflow as wf
    from app.orchestration.pov import Pov

    fake = await TestBeatsStageSeesThePlan()._run(monkeypatch)
    fake.responses["plan_beats"] = "[]"
    monkeypatch.setattr(wf, "get_llm_client", lambda: fake)
    scene = {"scene_description": "Intake.", "characters": ["Milo Voss"]}
    no_plan = dict(chapter_summary="", story_position=None, plan=[], earlier=[])
    await wf.plan_scene_beats(
        scene, canon_block="", pov=Pov("first person"), secondary_block="", **no_plan
    )
    assert set(fake.formats) == {str(JSON_OBJECT)}
    assert len(fake.calls) == 5
    for purpose in ("outline:chapters", "outline:beats", "plan_beats"):
        assert '{"items": [' in fake.prompts[purpose][0], purpose
