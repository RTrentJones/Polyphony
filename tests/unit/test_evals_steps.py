"""Unit tests for the eval steps' contract with the app — the defects seen in
the weekly live run:

(a) extraction scored 0: the step graded the source's committed cast, but
    extraction only PROPOSES (nothing is on the source until review/commit), so
    it read an empty list. It must grade the run's `proposals.characters`. And
    the cel corpus uploaded a 389-char excerpt that never names its cast.
(b) attribution got HTTP 422: characters are book-scoped and the step created
    them without a book_id.
"""

import json
import re
from unittest.mock import AsyncMock

import pytest

from evals.harness import embed
from evals.harness.cache import Cache
from evals.harness.client import EvalClientError, PolyphonyClient
from evals.steps import pipeline
from evals.steps.base import StepContext

pytestmark = pytest.mark.unit


class _Judge:
    async def score(self, rubric, text):  # pragma: no cover - not used here
        raise AssertionError("no judge call expected")


class _FakeClient:
    """Answers like the real API: characters 422 without a book_id; the source
    has no characters until the extraction run is committed."""

    def __init__(self, proposals: dict):
        self.proposals = proposals
        self.committed: list[str] = []
        self.created: list[tuple[str, str]] = []
        self.prompts: list[str] = []

    async def upload_source(self, filename, content, title, book_id=None):
        return {"id": "src", "book_id": "book-1", "extraction_run_id": "run-1"}

    async def wait_extraction(self, book_id, run_id, timeout=600):
        assert (book_id, run_id) == ("book-1", "run-1")
        return {"status": "ready", "proposals": self.proposals}

    async def commit_extraction(self, book_id, run_id, proposals, synopsis=False):
        self.committed = [c["name"] for c in proposals.get("characters", [])]
        return {"ok": True}

    async def source_characters(self, source_id):
        return [{"name": n} for n in self.committed]

    async def create_book(self, title, synopsis, genre=""):
        return {"id": "book-2"}

    async def create_character(self, name, book_id, **fields):
        if not book_id:
            raise EvalClientError("create character failed (422): book_id missing")
        self.created.append((name, book_id))
        return {"id": f"char-{name}"}

    async def add_voice_samples(self, character_id, samples, chunk_type="dialogue"):
        return {"added": len(samples)}

    async def test_dialogue(self, character_id, prompt, context=None):
        self.prompts.append(prompt)
        return {"dialogue": f"line from {character_id}"}


def _ctx(tmp_path, client, gt, text="corpus"):
    return StepContext(
        book="t",
        corpus_text=text,
        ground_truth=gt,
        cache=Cache(str(tmp_path), "sha"),
        judge=_Judge(),
        client=client,
    )


async def test_extraction_grades_the_runs_proposals_not_the_source(tmp_path):
    gt = {"cast": ["Milo Voss", "Zara Okafor", "Edric Thane"]}
    client = _FakeClient(
        {
            "characters": [
                {"name": "Milo Voss", "selected": True},
                {"name": "Zara Okafor", "selected": True},
                {"name": "Edric Thane", "selected": False},
            ]
        }
    )
    out = await pipeline.step_extraction(_ctx(tmp_path, client, gt))
    assert out["score"] == pytest.approx(1.0)
    assert out["recall"] == 1.0 and out["precision"] == 1.0
    # The default selection (one-window names pre-deselected) is graded too.
    assert out["selected"] == ["Milo Voss", "Zara Okafor"]
    assert out["commit_faithful"] is True


async def test_extraction_with_no_proposals_scores_zero_without_crashing(tmp_path):
    gt = {"cast": ["Milo Voss"]}
    out = await pipeline.step_extraction(_ctx(tmp_path, _FakeClient({}), gt))
    assert out["score"] == 0.0 and out["predicted"] == []


def test_proposed_characters_tolerates_shapes():
    run = {
        "proposals": {
            "characters": [{"name": "A"}, "B", {"name": "  "}, {"role": "x"}, None]
        }
    }
    assert [c["name"] for c in pipeline.proposed_characters(run)] == ["A", "B"]
    assert pipeline.proposed_characters({}) == []
    assert pipeline.proposed_characters({"proposals": None}) == []


def test_cel_uploads_a_manuscript_that_names_its_whole_cast():
    text, gt = pipeline.load_corpus("cel")
    for name in gt["cast"]:
        assert name in text, f"{name} is graded but never in the uploaded text"


@pytest.mark.parametrize("book", ["dracula", "frankenstein"])
def test_corpora_without_a_synopsis_file_upload_their_excerpts(book):
    text, _ = pipeline.load_corpus(book)
    excerpts = (pipeline.CORPORA / book / "excerpts.txt").read_text(encoding="utf-8")
    assert text == excerpts


async def test_attribution_creates_book_scoped_characters(tmp_path, monkeypatch):
    async def fake_many(texts):
        return [[1.0, 0.0] for _ in texts]

    async def fake_one(text):
        return [1.0, 0.0]

    monkeypatch.setattr(embed, "embed_many", fake_many)
    monkeypatch.setattr(embed, "embed_one", fake_one)
    _, cel = pipeline.load_corpus("cel")
    gt = {"synopsis": "s", "voice_probe": cel["voice_probe"], "gold_lines": {}}
    for who in ("Milo Voss", "Edric Thane"):
        gt["gold_lines"][who] = {"train": ["a"], "test": ["b"]}
    client = _FakeClient({})
    out = await pipeline.step_attribution(_ctx(tmp_path, client, gt))
    assert client.created == [("Milo Voss", "book-2"), ("Edric Thane", "book-2")]
    assert "error" not in out
    # M3: each in their own role; Edric reviews, Milo is reviewed.
    milo, edric = cel["voice_probe"]["Milo Voss"], cel["voice_probe"]["Edric Thane"]
    # (F) D13: three draws each, memoised per draw; the majority per character.
    assert client.prompts == [milo] * 3 + [edric] * 3
    assert "your desk for review" in edric and out["generated"] == 6
    assert [d["probe"] for d in out["detail"]] == [milo] * 3 + [edric] * 3
    assert out["per_character"] == {  # identical vectors: Milo ranks first
        "Milo Voss": {"correct": 3, "majority": True},
        "Edric Thane": {"correct": 0, "majority": False},
    }


async def test_client_refuses_a_character_without_a_book():
    c = PolyphonyClient("http://localhost:8000")
    c._http.post = AsyncMock()
    with pytest.raises(EvalClientError, match="book_id is required"):
        await c.create_character("Nora", "")
    c._http.post.assert_not_awaited()
    await c.aclose()


async def test_client_sends_book_id_in_the_create_body():
    import httpx

    c = PolyphonyClient("http://localhost:8000")
    c._http.post = AsyncMock(
        return_value=httpx.Response(
            201, json={"id": "x"}, request=httpx.Request("POST", "http://t")
        )
    )
    await c.create_character("Nora", "book-9", role="supporting")
    body = c._http.post.await_args.kwargs["json"]
    assert body == {"name": "Nora", "book_id": "book-9", "role": "supporting"}
    await c.aclose()


# --- Eval harness v2 (SPEC-22): the cel canon, the product path, the rubrics ---

_RUBRIC_SCORES = {
    "R1": 1.0,
    "R2": 0.8,
    "R3": 0.6,
    "R4": 0.9,
    "R5": 0.7,
    "R6": 0.5,
    "RA": 0.8,
}


class _RubricJudge:
    """Scores each rubric at a fixed value and keeps what it was shown."""

    def __init__(self):
        self.seen: list[tuple[str, str]] = []

    async def score(self, rubric, content):
        from types import SimpleNamespace

        rid = rubric.split(".")[0].replace("### ", "")
        self.seen.append((rid, content))
        return SimpleNamespace(score=_RUBRIC_SCORES[rid], explanation=f"{rid} ok")


BEAT_A = "Milo initialled the form. Zara did not look up from her screen at all."
BEAT_B_COPY = "Zara did not look up from her screen at all. The queue doubled by noon."
BEAT_B_CLEAN = '"Read it," Zara said. "Then thank no one." Milo read it.'
SCENE_1 = f"{BEAT_A}\n\n{BEAT_B_COPY}"
SCENE_2 = (
    '"Is that normal?" Milo asked. "Zara, is it?"\n\n'
    '"No," said Zara Okafor. "Bring nothing."'
)


class _ProductClient:
    """The API surface the v2 prose/outline steps drive, answering like the
    app: ids are per book, promote returns chapters, generate-planned returns
    the chapter's scenes."""

    def __init__(self, *, two_principal_chapter=True, outline_nodes=None):
        self.n = 0
        self.chars: dict[str, dict] = {}
        self.ids: dict[str, dict[str, str]] = {}
        self.style: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.two = two_principal_chapter
        self.outline_nodes = outline_nodes

    async def create_book(self, title, synopsis, genre=""):
        self.n += 1
        bid = f"book-{self.n}"
        self.ids[bid] = {}
        self.calls.append(("create_book", bid, title))
        return {"id": bid, "title": title, "synopsis": synopsis}

    async def create_character(self, name, book_id, **fields):
        cid = f"{book_id}:{name}"
        self.ids[book_id][name] = cid
        self.chars[cid] = {"id": cid, "name": name, "book_id": book_id, **fields}
        return {"id": cid}

    async def set_style(self, book_id, style):
        self.style[book_id] = {k: v for k, v in style.items() if k[0] != "_"}

    async def get_book(self, book_id):
        return {"id": book_id, "title": "Bored to Undeath", "synopsis": "s"}

    async def list_characters(self, book_id):
        return [{"id": c, "name": n} for n, c in self.ids[book_id].items()]

    async def get_character(self, cid):
        return {**self.chars[cid], "voice_stats": {"total_chunks": 4}}

    async def voice_chunks(self, cid, chunk_type=None):
        said = [{"chunk_type": "dialogue", "word_count": n} for n in (2, 3, 9)]
        return [*said, {"chunk_type": "action", "text": "He sat.", "word_count": 9}]

    async def get_canon(self, book_id):
        return {"entries": [], "style": self.style.get(book_id)}

    async def upload_source(self, filename, content, title, book_id=None):
        self.calls.append(("upload", book_id, content.decode()[:40]))
        return {"id": "src-v", "book_id": book_id, "extraction_run_id": "run-v"}

    async def wait_extraction(self, book_id, run_id, timeout=600):
        return {"status": "ready", "proposals": {"characters": [{"name": "Prava"}]}}

    async def commit_extraction(self, book_id, run_id, proposals, synopsis=False):
        self.calls.append(("commit", book_id, proposals))

    async def wait_voice_indexed(self, source_id, timeout=300):
        book = self.calls[-1][1]
        return [{"id": c, "name": n} for n, c in self.ids[book].items()]

    async def generate_outline(self, book_id, chapters_target=8, *, premise=None):
        self.calls.append(("outline", book_id, premise))
        ids = self.ids[book_id]
        nodes = self.outline_nodes or [
            {
                "title": "Milo is retained",
                "summary": "Milo Voss joins Reconciliations.",
                "character_ids": [ids["Milo Voss"]],
                "children": [{"title": "Intake", "summary": "Prava onboards him."}],
            },
            {
                "title": "The Lich",
                "summary": "Zara Okafor and Milo meet Edric Thane.",
                "character_ids": [ids["Milo Voss"], ids["Zara Okafor"]],
                "children": [],
            },
        ]
        return {"content": nodes, "status": "ready", "premise": {"acts": []}}

    async def put_outline(self, book_id, content):
        self.calls.append(("put_outline", book_id, content))

    async def promote_all(self, book_id):
        self.calls.append(("promote", book_id))
        self.book = book_id
        return {
            "chapters": [
                {"chapter_id": "ch-2", "position": 1},
                {"chapter_id": "ch-1", "position": 0},
            ]
        }

    async def get_chapter(self, chapter_id):
        ids = self.ids[self.book]
        cast = [ids["Milo Voss"]]
        if chapter_id == "ch-2" and self.two:
            cast.append(ids["Zara Okafor"])
        return {"title": f"title {chapter_id}", "summary": "s", "character_ids": cast}

    n_scenes = 2

    async def generate_planned(self, chapter_id, body=None):
        self.calls.append(("generate_planned", chapter_id))
        self.spent = 1000
        ids = [{"id": f"s{i + 1}"} for i in range(self.n_scenes)]
        return {
            "total": self.n_scenes,
            "queued": self.n_scenes,
            "deferred": 0,
            "reason": "batch reason",
            "estimate": {"tokens": 1200},
            "scenes": ids,
        }

    spent = 0

    async def create_chapter(self, book_id, title, summary=""):
        return {"id": "ch-new"}

    async def usage(self):
        return {"tokens_used_24h": 500 + self.spent}

    async def get_scene(self, sid):
        second = BEAT_B_COPY if sid == "s1" else BEAT_B_CLEAN
        return {
            "status": "completed",
            "content": SCENE_1 if sid == "s1" else SCENE_2,
            "characters": ["Milo Voss", "Zara Okafor"],
            "scene_request": {"scene_description": f"brief {sid}"},
            "name_warnings": ["Gary"] if sid == "s1" else [],
            # GET /scenes/{id} carries each inner beat (W1-S9).
            "beats": [
                {"beat_number": 0, "description": "Milo signs", "content": BEAT_A},
                {"beat_number": 1, "description": "Zara warns", "content": second},
            ],
        }

    async def scene_beats(self, sid):
        return (await self.get_scene(sid)).get("beats") or []


def _cel_ctx(tmp_path, client, judge=None):
    text, gt = pipeline.load_corpus("cel")
    return StepContext(
        book="cel",
        corpus_text=text,
        ground_truth=gt,
        cache=Cache(str(tmp_path), "sha"),
        judge=judge or _RubricJudge(),
        client=client,
    )


def test_cel_cast_is_the_real_canon():
    _, gt = pipeline.load_corpus("cel")
    cast = {c["name"]: c for c in pipeline._cast_with_roles(gt)}
    assert cast["Milo Voss"]["role"] == "protagonist"
    assert "deuteragonist" in cast["Zara Okafor"]["role"]
    assert cast["Edric Thane"]["role"] == "antagonist"
    assert "the CEL" in cast["Edric Thane"]["aliases"]
    for c in cast.values():
        assert "from the corpus" not in c["description"]
        assert c["goals"] and c["voice_characteristics"]["diction"]
        assert "voice" not in c["voice_characteristics"]  # F7: no "Voice: voice:"


def test_corpora_without_entries_keep_the_placeholder_cast():
    gt = {"cast": ["Nora", "Elias"], "fidelity": {"principals": [{"name": "Nora"}]}}
    cast = pipeline._cast_with_roles(gt)
    assert cast[0] == {
        "name": "Nora",
        "role": "protagonist",
        "description": "Nora, from the corpus.",
    }
    assert cast[1]["role"] == "supporting"


def test_cel_excerpt_carries_speaker_labelled_voice():
    _, gt = pipeline.load_corpus("cel")
    excerpt = pipeline.load_excerpts("cel")
    assert 4000 <= len(excerpt) <= 6500
    gold = gt["gold_lines"]
    assert set(gold) == {"Milo Voss", "Zara Okafor", "Edric Thane"}
    for name, v in gold.items():
        lines = v["train"] + v["test"]
        assert len(lines) >= 5, name  # whole utterances: Edric has five
        assert v["test"], name
        for line in lines:  # joined across its tag (SPEC-39): each sentence is there
            for part in re.split(r"(?<=[.?!]) ", line):
                assert part[:-1] in excerpt, (name, line, part)
    for lines in gt["other_lines"].values():
        assert all(line in excerpt for line in lines)
    style = pipeline.load_style("cel")
    assert style["tense"] == "past" and "Milo" in style["pov"]
    assert style["sample_prose"].count("\n\n") >= 1


def test_cel_plants_five_contradictions_in_the_excerpt():
    from evals.graders import continuity

    _, gt = pipeline.load_corpus("cel")
    assert gt["continuity_source"] == "excerpts"
    excerpt = pipeline.load_excerpts("cel")
    injected, expected = continuity.apply_injections(
        excerpt, gt["continuity_injections"]
    )
    assert len(expected) == 5 and injected != excerpt
    # Each planted error leaves the original fact standing elsewhere.
    for inj in gt["continuity_injections"]:
        assert inj["replace"] in injected
        if inj["id"] not in ("dead-speaks", "timeline-inversion"):
            assert inj["locate"] in injected, inj["id"]


async def test_continuity_runs_on_the_excerpt_for_cel(tmp_path):
    seen, books = [], []

    class Client(_ProductClient):
        async def create_book(self, title, synopsis, genre=""):
            books.append(synopsis)
            self.ids[title] = {}
            return {"id": title}

        async def create_chapter(self, book_id, title, summary=""):
            books.append(summary)
            return {"id": book_id}

        async def create_scene(self, chapter_id, content="", characters=None):
            seen.append(content)

        async def run_continuity(self, book_id, chapter_id=None):
            if "injected" in book_id:
                return [{"detail": "The memo's signature", "quote": "Edwin Thane"}]
            return []

    out = await pipeline.step_continuity(_cel_ctx(tmp_path, Client()))
    assert out["n_injected"] == 5 and out["detected"] == ["broken-alias"]
    assert out["precision"] == 1.0 and out["quote_verbatim"] == 1.0
    assert "Halvard Bell" in seen[0] and "BORED TO UNDEATH" not in seen[0]
    # W3-7: graded against the canon an author would have.
    _, gt = pipeline.load_corpus("cel")
    summary = gt["canonical_beats"][0]["title"]
    assert books == [gt["synopsis"], summary] * 2


def test_strip_tags_leaves_only_spoken_lines_with_names_masked():
    text = (
        'Zara set down her pen. "Read it, Milo," said Zara Okafor.\n\n'
        "“Is that normal?” Milo asked."
    )
    assert pipeline.strip_tags(text, ["Milo Voss", "Milo", "Zara Okafor", "Zara"]) == [
        "Read it, [name],",
        "Is that normal?",
    ]


async def test_prose_writes_the_planned_scenes_of_a_two_principal_chapter(tmp_path):
    client = _ProductClient()
    judge = _RubricJudge()
    out = await pipeline.step_prose(_cel_ctx(tmp_path, client, judge))

    assert out["path"] == "planned"
    # ch-1 casts only Milo; ch-2 (Milo + Zara) is the one written, in order.
    assert ("generate_planned", "ch-2") in client.calls
    assert not any(c[0] == "generate_scene" for c in client.calls)
    # The voice excerpt went into the prose book, and the commit approved the
    # canon cast by name only (curated entries are not overwritten).
    upload = next(c for c in client.calls if c[0] == "upload")
    commit = next(c for c in client.calls if c[0] == "commit")
    assert upload[1] == commit[1] and upload[2].startswith("Milo Voss opened")
    assert all(set(c) == {"name"} for c in commit[2]["characters"])
    assert out["voice"]["chunks"]["Zara Okafor"] == 4
    assert out["voice"]["speech"]["Edric Thane"] == 2  # >= 3-word dialogue (D9)
    assert out["voice"]["movement"]["Edric Thane"] == ["He sat."]  # D6 reads it

    s1, s2 = out["scenes"]
    # Scene 1 repeats a sentence across its beat seam: R4 is capped at 0.
    assert s1["beat_seam_max_overlap"] >= 1
    assert s1["rubrics"]["R4"]["score"] == 0.0 and "R4" in s1["deterministic_failures"]
    assert s1["score"] == pytest.approx(1.0 * (0.8 + 0.6 + 0.0 + 0.7) / 4)
    assert s2["beat_seam_max_overlap"] == 0 and s2["deterministic_failures"] == {}
    assert s2["score"] == pytest.approx(1.0 * (0.8 + 0.6 + 0.9 + 0.7) / 4)
    assert out["score"] == pytest.approx((s1["score"] + s2["score"]) / 2, abs=1e-4)
    assert out["cast_named"] == 1.0 and out["words"] > 0
    # (F) Reported beside unknown_canon: the app's stored warning (W6-3). The
    # unplaced events are the signed file's, in prompt_contract (W7-2, D4).
    assert [s["name_warnings"] for s in out["scenes"]] == [["Gary"], []]
    assert "unplaced_events" not in out

    shown = dict(judge.seen[-5:])
    # The judge sees the canon, the style guide's sample prose and the beats...
    assert "Milo initialled the form in the box marked INITIAL HERE" in shown["R3"]
    assert "PLANNED BEATS (in order):\n1. Milo signs\n2. Zara warns" in shown["R3"]
    assert "Chief Executive Lich" in shown["R1"]
    # ...and for R2, only the untagged lines plus each speaker's own samples.
    assert "said Zara" not in shown["R2"] and "1. Is that normal?" in shown["R2"]
    assert "2. [name], is it?" in shown["R2"]
    assert "Bring nothing. Sign nothing." in shown["R2"]


def test_r1_requires_someone_of_the_cast_on_the_page():
    _, gt = pipeline.load_corpus("cel")
    forms = pipeline._forms(gt)
    scene = {
        "content": "Milo signed. Zara watched him sign.",
        "characters": ["Milo Voss", "Zara Okafor", "Edric Thane"],
        "description": "Zara trains Milo",
        "beats": [{"description": "Milo signs", "content": "Milo signed."}],
    }
    # Edric is in the chapter's cast but not in this scene's plan.
    checks = pipeline._grade_scene_checks(scene, "", forms)
    assert checks["caps"] == {} and checks["warnings"] == []
    assert checks["cast_recall"] == pytest.approx(2 / 3, abs=1e-3)
    # The plan says the Lich is there and the prose never names him: a warning.
    scene["beats"].append({"description": "The Lich summons Milo", "content": ""})
    checks = pipeline._grade_scene_checks(scene, "", forms)
    assert "R1" not in checks["caps"]
    assert checks["warnings"] == ["Edric Thane is in the plan but never named"]


async def test_a_failed_planned_path_fails_loudly_and_is_tried_again(tmp_path):
    """M16 (SPEC-39): no fallback scene; the failure is reported, scored None
    and not memoised (S5), so the next run takes the planned path."""
    out = await pipeline.step_prose(
        _cel_ctx(tmp_path, _ProductClient(two_principal_chapter=False))
    )
    assert out["path"] == "failed" and "two principals" in out["reason"]
    assert out["score"] is None and "fallback" not in json.dumps(out)
    again = await pipeline.step_prose(_cel_ctx(tmp_path, _ProductClient()))
    assert again["path"] == "planned" and again["score"] is not None
    assert (again["queued"], again["deferred"], again["reason"]) == (
        2,
        0,
        "batch reason",
    )
    assert again["estimate_over_actual"] == 1.2


@pytest.mark.parametrize("n, judged", [(4, 4), (7, 5)])
async def test_the_judge_grades_every_planned_scene_up_to_five(tmp_path, n, judged):
    client = _ProductClient()
    client.n_scenes = n
    judge = _RubricJudge()
    out = await pipeline.step_prose(_cel_ctx(tmp_path, client, judge))
    assert len(out["scenes"]) == n
    assert sum(1 for rid, _ in judge.seen if rid == "R1") == judged


async def test_prose_reuses_the_memoised_outline_in_its_own_book(tmp_path):
    client = _ProductClient()
    ctx = _cel_ctx(tmp_path, client)
    await pipeline.step_outline(ctx)
    await pipeline.step_prose(ctx)
    assert sum(1 for c in client.calls if c[0] == "outline") == 1
    (put,) = [c for c in client.calls if c[0] == "put_outline"]
    prose_book = next(c[1] for c in client.calls if c[0] == "promote")
    assert put[1] == prose_book
    # The stored outline names its cast (the outline book's ids would 400).
    assert put[2][1]["characters"] == ["Milo Voss", "Zara Okafor"]
    assert "character_ids" not in put[2][1]


async def test_outline_confirms_the_authors_premise_and_caps_r1(tmp_path):
    client = _ProductClient(
        outline_nodes=[
            {
                "title": "Elara arrives",
                "summary": "Milo Voss meets Elara, Zara Okafor and Prava.",
                "children": [],
            }
        ]
    )
    judge = _RubricJudge()
    out = await pipeline.step_outline(_cel_ctx(tmp_path, client, judge))
    (outline_call,) = [c for c in client.calls if c[0] == "outline"]
    _, gt = pipeline.load_corpus("cel")
    assert outline_call[2] == gt["premise_edit"]
    assert out["premise_edited"] is True
    # Edric Thane is missing: R1 is capped at 0 whatever the judge said.
    assert out["missing_principals"] == ["Edric Thane"]
    assert out["rubrics"]["R1"]["score"] == 0.0 and out["score"] == 0.0
    # Prava is in the synopsis; Elara is invented.
    assert out["unknown_canon"] == ["Elara"]
    assert [rid for rid, _ in judge.seen] == ["R1", "R6", "R3"]
    assert gt["premise_edit"]["premise_restated"] in judge.seen[0][1]
    assert "premise_restated" not in judge.seen[0][1]


async def test_outline_score_is_r1_times_mean_r6_r3(tmp_path):
    out = await pipeline.step_outline(_cel_ctx(tmp_path, _ProductClient()))
    assert out["cast_fidelity"] == 1.0 and out["deterministic_failures"] == {}
    assert out["score"] == pytest.approx(1.0 * (0.5 + 0.6) / 2)


# --- SPEC-28 (wave 2): H1 cap, F8 fixture, §5.2 deterministic checks ---------------


def _cel_forms():
    return pipeline._forms(pipeline.load_corpus("cel")[1])


def _intake_scene(counter):
    """Milo and Zara are cast, and the plan puts Zara in beat 2."""
    return {
        "content": f"Milo blinked at the org chart.\n\n{counter}",
        "characters": ["Milo Voss", "Zara Okafor"],
        "description": "Mid-Sentence on a Tuesday",
        "beats": [{"description": "Zara Okafor tells him to read the appendices"}],
    }


def test_r1_an_unnamed_planned_principal_is_a_warning_not_a_cap():
    """S4: "the caseworker said" credits nobody, and the cap needs the whole
    cast absent; the judge's R1 stands."""
    scene = _intake_scene('"Read the appendices," the caseworker said.')
    checks = pipeline._grade_scene_checks(scene, "", _cel_forms())
    assert checks["caps"] == {}
    assert checks["warnings"] == ["Zara Okafor is in the plan but never named"]


def test_r1_is_capped_when_none_of_the_cast_is_named():
    scene = _intake_scene("The office was cold.")
    scene["content"] = "The office was cold."
    checks = pipeline._grade_scene_checks(scene, "", _cel_forms())
    assert checks["caps"]["R1"] == "none of the scene's cast is on the page"


def test_r1_an_honorific_and_surname_name_her():
    scene = _intake_scene('"Read it," Ms. Okafor said. Zara\'s pen stopped.')
    checks = pipeline._grade_scene_checks(scene, "", _cel_forms())
    assert checks["caps"] == {} and checks["warnings"] == []
    assert checks["cast_recall"] == 1.0
    assert pipeline.named_in("Ms. Okafor said.", ["Zara Okafor", "Zara"])
    assert not pipeline.named_in("The okafor file.", ["Zara Okafor"])


def test_cel_premise_agrees_with_the_synopsis_ending():
    """F8: the confirmed premise ends as the synopsis does."""
    _, gt = pipeline.load_corpus("cel")
    premise = gt["premise_edit"]["premise_restated"]
    assert "files it correctly" in premise and "goes onward" in premise
    assert "end him" not in premise
    assert "files it correctly" in gt["synopsis"]
    assert gt["secondary_cast"] == ["Prava", "Old Fen", "Halvard Bell"]
    for name in gt["secondary_cast"]:
        assert name not in gt["cast"] and name not in gt["fidelity"]["known"]
    assert "secondaries" not in gt["fidelity"]


def test_unknown_canon_counts_the_secondary_cast_as_known():
    """W3-7: Halvard Bell is in the author's manuscript, not an invention."""
    _, gt = pipeline.load_corpus("cel")
    _, unknown = pipeline._unknown_canon("Milo closed the Halvard Bell account.", gt)
    assert unknown == []


def test_a_secondarys_own_other_line_is_own():
    """W3-1/E4a: a secondary's voice samples come from other_lines."""
    from evals.graders.prompt_contract import classify_sample

    _, gt = pipeline.load_corpus("cel")
    gold = {k: v["train"] + v["test"] for k, v in gt["gold_lines"].items()}
    sample = "Nobody reads the appendices. That's what they're for."
    assert classify_sample("Old Fen", sample, gold, gt["other_lines"]) == "own"


def test_extraction_depth_needs_every_principals_fields_and_mentions():
    forms = _cel_forms()
    full = {"description": "d", "goals": "g", "voice_characteristics": {"diction": "x"}}
    chars = [
        {"name": "Milo Voss", "mentions": 33, **full},
        {"name": "Zara", "aliases": ["Zara Okafor"], "mentions": 21, **full},
        {"name": "Edric Thane", "mentions": 37, **full, "goals": ""},
    ]
    out = pipeline.extraction_depth(chars, forms)
    assert out["principal_fields"] == "2/3"
    assert out["principal_fields_missing"] == {"Edric Thane": ["goals"]}
    # run-W25's counts: correct, and not in "principal order" (W3-7).
    assert out["principal_mentions_min"] == 21
    chars[2].update(goals="g", voice_characteristics={"diction": ""})
    out = pipeline.extraction_depth(chars, forms)
    assert out["principal_fields_missing"] == {"Edric Thane": ["voice_characteristics"]}
    assert out["canonical_names_ok"] == "1/1"  # Edric, the one with a title alias
    chars[2]["name"] = "Chief Executive Lich"
    chars[2]["aliases"] = ["Edric Thane"]
    assert pipeline.extraction_depth(chars, forms)["canonical_names_ok"] == "0/1"
    out = pipeline.extraction_depth(chars[:2], forms)
    assert out["principal_mentions_min"] == 0  # a missing principal: no mentions
    assert out["principal_fields_missing"]["Edric Thane"] == [
        "description",
        "goals",
        "voice_characteristics",
    ]


async def test_extraction_reports_principal_depth(tmp_path):
    full = {"description": "d", "goals": "g", "voice_characteristics": {"diction": "x"}}
    proposals = {
        "characters": [
            {"name": "Milo Voss", "mentions": 3, **full},
            {"name": "Zara Okafor", "mentions": 2, **full},
            {"name": "Edric Thane", "mentions": 1, **full},
        ]
    }
    _, gt = pipeline.load_corpus("cel")
    out = await pipeline.step_extraction(_ctx(tmp_path, _FakeClient(proposals), gt))
    assert out["principal_fields"] == "3/3" and out["principal_mentions_min"] == 1


def test_outline_secondaries_counts_text_and_secondary_names():
    nodes = [
        {
            "title": "Intake",
            "summary": "Prava onboards Milo.",
            "children": [{"title": "Records", "secondary_names": ["Old Fen"]}],
        }
    ]
    out = pipeline.outline_secondaries(nodes, ["Prava", "Old Fen"])
    assert out == {
        "outline_names_synopsis_secondaries": "2/2",
        "outline_secondaries_named": ["Prava", "Old Fen"],
    }
    assert pipeline.outline_secondaries([], ["Prava"])[
        "outline_names_synopsis_secondaries"
    ] == ("0/1")


async def test_the_outline_book_commits_the_secondaries_as_minor(tmp_path):
    """S6: as an author who accepted extraction's defaults; each described
    from the excerpt or the synopsis (the synopsis never names Bell)."""
    client = _ProductClient()
    await pipeline.step_outline(_cel_ctx(tmp_path, client))
    minor = {c["name"]: c for c in client.chars.values() if c.get("role") == "minor"}
    assert sorted(minor) == ["Halvard Bell", "Old Fen", "Prava"]
    assert all(c["description"] for c in minor.values())
    assert minor["Halvard Bell"]["description"].startswith("Halvard Bell had been")
    # (F) W6-5 (b): synopsis first, through `introduction`: no later plot.
    assert minor["Prava"]["description"] == (
        "There is Prava, the intake caseworker who onboarded Milo with such "
        "practiced warmth"
    )
    assert minor["Old Fen"]["description"].endswith("begun to file himself")


def test_outline_consistency_reports_people_and_title_reuse():
    """(F) W6-4, reported: Zara is in a scene but not its chapter's people,
    and a scene reuses a chapter title."""
    nodes = [
        {"title": "One", "people": ["Milo Voss"], "children": [{"title": "Two"}]},
        {"title": "Two", "children": [{"title": "B", "people": ["Zara Okafor"]}]},
    ]
    assert pipeline.outline_consistency(nodes) == {
        "people_mismatches": ["B: Zara Okafor"],
        "title_reuse": ["Two"],
    }


async def test_outline_step_reports_synopsis_secondaries(tmp_path):
    out = await pipeline.step_outline(_cel_ctx(tmp_path, _ProductClient()))
    # The default outline names Prava (beat "Intake"), not Old Fen.
    assert out["outline_names_synopsis_secondaries"] == "1/2"


BEAT_INTAKE = {
    "node_id": "11111111-1111-4111-8111-111111111111",
    "title": "Intake",
    "summary": "Prava onboards Milo.",
    "character_ids": [],
    "secondary_names": ["Prava"],
    "setting": "Intake Atrium",
    "tone": "bureaucratic dread",
}


def test_scene_maps_to_its_planned_beat_and_cast_from_beat():
    chapter = {"cast": ["Milo Voss", "Zara Okafor"], "pov": "Milo Voss"}
    beat = {**BEAT_INTAKE, "characters": ["Milo Voss"]}
    other = {**BEAT_INTAKE, "node_id": "x", "title": "Desk", "characters": []}
    scene = {"planned_beat_id": BEAT_INTAKE["node_id"], "characters": ["Milo Voss"]}
    assert pipeline.planned_beat_of(scene, [other, beat]) is beat
    # Without the id, the scene description is the beat's.
    scene = {"description": "Intake: Prava onboards Milo.", "characters": ["Milo Voss"]}
    assert pipeline.planned_beat_of(scene, [other, beat]) is beat
    assert pipeline.cast_from_beat(scene, beat, chapter) is True
    # The chapter cast where the beat had its own: wrong.
    scene["characters"] = ["Milo Voss", "Zara Okafor"]
    assert pipeline.cast_from_beat(scene, beat, chapter) is False
    # A beat with no cast takes the chapter's.
    assert pipeline.cast_from_beat(scene, other, chapter) is True
    # The POV is always in.
    no_pov = {**beat, "characters": ["Zara Okafor"]}
    assert pipeline.cast_from_beat(scene, no_pov, chapter) is True
    assert pipeline.cast_from_beat(scene, None, chapter) is None


class _PlannedBeatsClient(_ProductClient):
    """The app after SPEC-23: chapters carry planned beats, scene requests the
    beat's id/setting/tone."""

    async def get_chapter(self, chapter_id):
        ch = await super().get_chapter(chapter_id)
        ids = self.ids[self.book]
        ch["pov_character_id"] = ids["Milo Voss"]
        ch["planned_beats"] = [
            {**BEAT_INTAKE, "character_ids": [ids["Milo Voss"]]},
            {**BEAT_INTAKE, "node_id": "n2", "title": "Desk", "character_ids": []},
        ]
        return ch

    async def get_scene(self, sid):
        scene = await super().get_scene(sid)
        if sid == "s1":
            scene["characters"] = ["Milo Voss"]
            scene["scene_request"].update(
                planned_beat_id=BEAT_INTAKE["node_id"],
                setting="Intake Atrium",
                emotional_tone="bureaucratic dread",
                pov_character="Milo Voss",
            )
        return scene


async def test_prose_records_planned_beats_and_judges_with_their_setting(tmp_path):
    client = _PlannedBeatsClient()
    judge = _RubricJudge()
    out = await pipeline.step_prose(_cel_ctx(tmp_path, client, judge))
    assert [b["title"] for b in out["planned_beats"]] == ["Intake", "Desk"]
    assert out["planned_beats"][0]["characters"] == ["Milo Voss"]
    s1, s2 = out["scenes"]
    assert s1["planned_beat"]["title"] == "Intake" and s1["cast_from_beat"] is True
    assert s2["planned_beat"] is None and s2["cast_from_beat"] is None
    assert out["cast_from_beat"] == 1.0
    assert out["r1_capped_scenes"] == 0
    assert s1["deterministic_warnings"] == []
    shown = [c for rid, c in judge.seen if rid == "R1"][0]
    assert "PLANNED BEAT: Intake" in shown and "BEAT CAST: Milo Voss" in shown
    assert "SETTING: Intake Atrium" in shown and "TONE: bureaucratic dread" in shown
    assert "BEAT SECONDARY PEOPLE: Prava" in shown


async def test_collect_scene_reads_beats_from_the_scene_api(tmp_path):
    ctx = _cel_ctx(tmp_path, _PlannedBeatsClient())
    scene = await pipeline._collect_scene(ctx, "s1")
    assert [b["description"] for b in scene["beats"]] == ["Milo signs", "Zara warns"]
    assert scene["planned_beat_id"] == BEAT_INTAKE["node_id"]


REVISE_DRAFT = (
    "Milo initialled the form.\n\n"
    "Across the partition, very quietly and without any hurry at all, Zara did not "
    "look up. She turned a page of the ledger, then another, as though the new "
    "analyst were a draft she had not been asked to review.\n\n"
    '"Is it always this quiet?" Milo asked.\n\n'
    '"No," Zara said. "Sometimes it\'s quieter."'
)


# Beat 1 is the first two paragraphs, beat 2 the exchange.
REVISE_BEATS = [
    {"content": "\n\n".join(REVISE_DRAFT.split("\n\n")[:2])},
    {"content": "\n\n".join(REVISE_DRAFT.split("\n\n")[2:])},
]


def test_tighten_the_longest_paragraph_of_beat_1_then_ask_the_rest():
    """(F) W6-7 (c): the longest beat-1 paragraph of >= 30 words, so the redo
    meets beat 2 as written; else the scene's longest; else none (W5b's
    five-word target). B-4: no keep clause."""
    long, longer = " ".join(["He read the ledger."] * 8), " ".join(["Zara."] * 40)
    draft = f"{long}\n\nShort.\n\n{longer}"
    beats = [{"content": f"{long}\n\nShort."}, {"content": longer}]
    for bs, text in ((beats, long), ([{"content": "Short."}], longer), ([], longer)):
        got = pipeline.tighten_request(draft, bs)["range"]
        assert draft[got["start"] : got["end"]] == text
    assert pipeline.tighten_request("Short.\n\nAlso short.", []) is None
    reqs = pipeline.revise_requests(REVISE_DRAFT, has_beats=True)
    assert [r["id"] for r in reqs] == ["colder", "redo_beat"]
    colder = reqs[0]["range"]
    assert REVISE_DRAFT[colder["start"] : colder["end"]].startswith('"No," Zara')
    assert reqs[1] == {
        "id": "redo_beat",
        "instruction": "Redo beat 2: let Milo notice one thing about the room "
        "that unsettles him.",
        "beat_index": 1,
    }
    assert "redo_beat" not in [
        r["id"] for r in pipeline.revise_requests(REVISE_DRAFT, has_beats=False)
    ]


def test_revision_checks_outside_bytes_and_new_names():
    _, gt = pipeline.load_corpus("cel")
    start = REVISE_DRAFT.index("Across")
    end = REVISE_DRAFT.index("look up.") + len("look up.")
    good = REVISE_DRAFT[:start] + "Zara did not look up." + REVISE_DRAFT[end:]
    checks, caps = pipeline.revision_checks(REVISE_DRAFT, good, start, end, gt)
    assert checks["outside_identical"] and checks["new_names"] == [] and caps == {}
    # A one-word name that only opens a sentence is not reported (NB-1).
    opener = (
        REVISE_DRAFT[:start] + "Silence. Zara did not look up." + REVISE_DRAFT[end:]
    )
    assert pipeline.revision_checks(REVISE_DRAFT, opener, start, end, gt)[1] == {}
    invented = REVISE_DRAFT[:start] + "Zara ignored Gorvath." + REVISE_DRAFT[end:]
    checks, caps = pipeline.revision_checks(REVISE_DRAFT, invented, start, end, gt)
    assert checks["new_names"] == ["Gorvath"] and "Gorvath" in caps["R5"]
    leaked = good.replace("Milo initialled", "Milo signed")
    checks, caps = pipeline.revision_checks(REVISE_DRAFT, leaked, start, end, gt)
    assert checks["outside_identical"] is False and "RA" in caps
    # A span that ends at the draft's end (last paragraph, whole scene) has no
    # tail; a valid proposal for it is not capped.
    last = REVISE_DRAFT.rindex("\n\n") + 2
    tail_ok = REVISE_DRAFT[:last] + "Zara did not look up."
    checks, caps = pipeline.revision_checks(
        REVISE_DRAFT, tail_ok, last, len(REVISE_DRAFT), gt
    )
    assert checks["outside_identical"] and caps == {}
    assert checks["revised"] == "Zara did not look up."


class _ReviseClient(_ProductClient):
    """The revise API on one scene with two beats: a proposal is the draft
    without the paragraph's padding, and accepting it makes it the draft."""

    def __init__(self):
        super().__init__()
        self.draft, self.revised, self.discarded = REVISE_DRAFT, [], []
        self.proposals: dict[str, dict] = {}

    async def get_scene(self, sid):
        content = {"content": self.draft, "characters": ["Milo Voss"]}
        return {"id": sid, "book_id": "book-1", **content, "beats": REVISE_BEATS}

    async def create_scene(self, chapter_id, content="", characters=None):
        return {"id": "rev-scene"}

    async def revise_draft(self, sid):
        return self.draft

    async def revise(self, scene_id, body):
        self.revised.append({**body, "draft": self.draft})
        span = body.get("range") or {"start": self.draft.index('"Is it')}
        start, end = span["start"], span.get("end", len(self.draft))
        new = self.draft[start:end].replace(
            " very quietly and without any hurry at all,", ""
        )
        pid = f"p{len(self.revised)}"
        self.proposals[pid] = {
            "id": pid,
            "content": self.draft[:start] + new + self.draft[end:],
            "range": [start, end],
            "status": "proposed",
            "dropped_callbacks": ["B-flat"],
        }
        return {"job": {}, "proposal": self.proposals[pid], "draft": self.draft}

    async def accept_proposal(self, scene_id, revision_id):
        self.draft = self.proposals[revision_id]["content"]

    async def discard_proposal(self, scene_id, revision_id):
        self.discarded.append(revision_id)


async def test_revise_step_accepts_the_tighten_and_asks_the_rest_of_it(tmp_path):
    client = _ReviseClient()
    judge = _RubricJudge()
    ctx = _cel_ctx(tmp_path, client, judge)
    await ctx.client.create_book("x", "s")  # book-1 for the canon block
    out = await pipeline.step_revise(ctx)
    tighten, colder, redo = out["revisions"]
    assert tighten["accepted"] is True and client.discarded == ["p2", "p3"]
    accepted = client.proposals["p1"]["content"]
    assert [r["draft"] for r in client.revised] == [REVISE_DRAFT, accepted, accepted]
    assert colder["outside_identical"] and redo["dropped_callbacks"] == ["B-flat"]
    assert tighten["untouched_share"] == 0.5  # (F) D6: its second sentence kept
    assert redo["borrowed_phrase"] == []
    seen = [c for rid, c in judge.seen if rid == "RA"][0]
    assert "INSTRUCTION: Tighten this paragraph" in seen
    assert (
        "PASSAGE AFTER REVISION:\nAcross the partition, Zara did not look up." in seen
    )


async def test_revise_step_records_a_409_as_refused_and_goes_on(tmp_path):
    class Refusing(_ReviseClient):
        async def revise(self, scene_id, body):
            if "beat_index" in body:
                raise EvalClientError("revise failed (409): The beat was edited")
            return await super().revise(scene_id, body)

    ctx = _cel_ctx(tmp_path, Refusing())
    await ctx.client.create_book("x", "s")
    out = await pipeline.step_revise(ctx)
    tighten, colder, redo = out["revisions"]
    assert (redo["refused"], redo["score"]) == (True, None)
    assert "409" in redo["error"] and tighten["accepted"] is True
    assert out["score"] == pytest.approx((tighten["score"] + colder["score"]) / 2)


def test_portable_outline_swaps_ids_in_beat_children_too():
    """Beats carry their own cast since wave 2; ids from another book must not
    reach a strict outline PUT (it 400s: 'Not in this book's cast')."""
    from evals.steps.pipeline import portable_outline

    ids = {"Milo Voss": "m1", "Zara Okafor": "z1"}
    nodes = [
        {
            "title": "One",
            "character_ids": ["m1", "foreign"],
            "children": [{"title": "Intake", "character_ids": ["z1", "foreign"]}],
        }
    ]
    out = portable_outline(nodes, ids)
    assert out[0]["characters"] == ["Milo Voss"]
    child = out[0]["children"][0]
    assert "character_ids" not in child and child["characters"] == ["Zara Okafor"]
    assert nodes[0]["children"][0]["character_ids"] == ["z1", "foreign"]  # input kept
    # A POV the other book does not cast (the outline book's minor Bell) goes.
    two = portable_outline(
        [{"title": "A", "pov": "Halvard Bell"}, {"title": "B", "pov": "Milo Voss"}], ids
    )
    assert "pov" not in two[0] and two[1]["pov"] == "Milo Voss"
    # The outline book's minor cast ports as secondary names, not dropped.
    three = portable_outline(nodes, ids, {"foreign": "Halvard Bell"})
    assert three[0]["secondary_names"] == ["Halvard Bell"]
    assert three[0]["children"][0]["secondary_names"] == ["Halvard Bell"]
