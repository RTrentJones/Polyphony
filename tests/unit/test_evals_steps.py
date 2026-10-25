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
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from evals.graders import rubrics
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


@pytest.mark.parametrize("book", ["dracula", "frankenstein", "wuthering"])
def test_corpora_without_a_synopsis_file_upload_their_excerpts(book):
    text, _ = pipeline.load_corpus(book)
    excerpts = (pipeline.CORPORA / book / "excerpts.txt").read_text(encoding="utf-8")
    assert text == excerpts


@pytest.mark.parametrize("book", ["dracula", "frankenstein", "wuthering"])
def test_a_renamed_corpus_leaks_no_original_and_renames_one_to_one(book):
    """(F) W13-5: no original survives in the excerpt; one rename is one person
    (shared only by a name and its part); a rename is never an original name."""
    spec = json.loads((pipeline.CORPORA / book / "aliases.json").read_text())
    maps = [spec["aliases"], *spec.get("section_aliases", {}).values()]
    originals = {w for m in maps for k in m for w in k.split()} - {
        "Mrs.",
        "Lord",
        "Count",
    }
    for m in maps:
        for src, rep in m.items():
            assert not re.search(
                rf"\b{re.escape(src)}\b", pipeline.load_excerpts(book), re.I
            )
            same = [set(k.split()) for k, v in m.items() if v == rep]
            assert all(k <= set(src.split()) or set(src.split()) <= k for k in same)
            assert not set(rep.split()) & originals, rep


def test_dracula_card_is_signed_with_the_renamed_initial():
    """(F) W16-4: the Count (Vasska) signs his card "--V.", not "--D."."""
    text = pipeline.load_excerpts("dracula")
    assert "wait for me.--V.”" in text and "--D." not in text


async def test_attribution_creates_book_scoped_characters(tmp_path, monkeypatch):
    seen = []

    async def fake_many(texts):
        seen.extend(texts)
        return [[1.0, 0.0] for _ in texts]

    async def fake_one(text):
        seen.append(text)
        return [1.0, 0.0]

    monkeypatch.setattr(embed, "embed_many", fake_many)
    monkeypatch.setattr(embed, "embed_one", fake_one)
    _, cel = pipeline.load_corpus("cel")
    gt = {"synopsis": "s", "voice_situation": cel["voice_situation"], "gold_lines": {}}
    gt["cast"] = ["Milo Voss", "Edric Thane"]
    for who in ("Milo Voss", "Edric Thane"):
        gt["gold_lines"][who] = {"train": ["Mr. Voss, do sit."], "test": ["held out"]}
    client = _FakeClient({})
    out = await pipeline.step_attribution(_ctx(tmp_path, client, gt))
    assert client.created == [("Milo Voss", "book-2"), ("Edric Thane", "book-2")]
    assert "error" not in out
    # (F) W9-2 (a): one symmetric situation, ten draws each (k = 10), memoised
    # per draw; only the name and voice block differ, in the product's prompt.
    assert client.prompts == [cel["voice_situation"]] * 20 and out["generated"] == 20
    assert out["status"] == "reported" and out["w9_9_p0"] is False  # Milo 0.72
    # (F) W9-2 (b): masked on both sides, the gold line and the generated one.
    assert {"⟨NAME⟩, do sit.", "line from char-⟨NAME⟩"} <= set(seen)
    # (F) wave 11, D4 (a): the reference centroids are the TRAIN lines; no
    # held-out test line is embedded, and the report says so.
    assert "held out" not in seen and out["reference"] == "train"
    assert out["per_character"] == {  # identical vectors: Milo ranks first
        "Milo Voss": {"correct": 10, "k": 10, "accuracy": 1.0, "wilson95": (0.72, 1.0)},
        "Edric Thane": {
            "correct": 0,
            "k": 10,
            "accuracy": 0.0,
            "wilson95": (0.0, 0.28),
        },
    }


async def test_attribution_cycles_a_corpus_neutral_probe_set(tmp_path, monkeypatch):
    """(F) W16-3: an opted-in corpus cycles its everyday prompts across the
    draws, the same set for every character; frankenstein opts in."""

    async def fake_many(texts):
        return [[1.0, 0.0] for _ in texts]

    async def fake_one(text):
        return [1.0, 0.0]

    monkeypatch.setattr(embed, "embed_many", fake_many)
    monkeypatch.setattr(embed, "embed_one", fake_one)
    neutral = pipeline.load_corpus("frankenstein")[1]["voice_situations"]
    assert len(neutral) >= 3
    gt = {"synopsis": "s", "voice_situation": "polar", "voice_situations": neutral}
    gt["gold_lines"] = {w: {"train": ["a"], "test": ["b"]} for w in ("A", "B")}
    client = _FakeClient({})
    out = await pipeline.step_attribution(_ctx(tmp_path, client, gt))
    draws = [neutral[d % len(neutral)] for d in range(pipeline.ATTRIBUTION_DRAWS)]
    assert client.prompts == draws * 2 and out["probe_set"] == "neutral"
    assert [r["probe"] for r in out["detail"]] == draws * 2
    assert "normalised" in out and "polar" not in client.prompts


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
    "RN": 0.3,
    "RC": 0.2,
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

    def __init__(self, *, w9b_shape=False, planned_fails=False, outline_nodes=None):
        self.n = 0
        self.chars: dict[str, dict] = {}
        self.ids: dict[str, dict[str, str]] = {}
        self.style: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.w9b_shape, self.planned_fails = w9b_shape, planned_fails  # W10-3 (a)
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

    async def scene_grounding(self, scene_id):  # one withheld line (SPEC-69)
        zara = {"name": "Zara Okafor", "withheld": [{"text": "x", "rule": "r"}]}
        return {"beats": [{"description": scene_id, "characters": [zara]}]}

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
        if (chapter_id == "ch-2") == self.w9b_shape:  # the chapter casting two
            cast.append(ids["Zara Okafor"])
        return {"title": f"title {chapter_id}", "summary": "s", "character_ids": cast}

    n_scenes = 2

    async def generate_planned(self, chapter_id, body=None):
        self.calls.append(("generate_planned", chapter_id))
        if self.planned_fails:
            raise EvalClientError("422: the chapter has no planned beats")
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


def test_r2_lines_are_the_ones_credited_to_the_scene_cast():
    # (F) W15-3: an invented crewman's line and a pronoun-tagged line credit no
    # cast member, so R2 never sees them; a vocative is masked.
    text = (
        'Zara set down her pen. "Read it, Milo," said Zara Okafor.\n\n'
        "“Is that normal?” Milo asked.\n\n"
        '"Ice ahead, sir," said the bosun.\n\n"Hold fast," she said.'
    )
    got = pipeline.cast_spoken_lines(text, ["Milo Voss", "Zara Okafor"], _cel_forms())
    assert got == [
        {"speaker": "Zara Okafor", "line": "Read it, Milo,"},
        {"speaker": "Milo Voss", "line": "Is that normal?"},
    ]
    assert pipeline.mask_names(got[0]["line"], ["Milo Voss", "Milo"]) == (
        "Read it, [name],"
    )


class _SpeakerJudge(_RubricJudge):
    def __init__(self, *speakers):
        super().__init__()
        self.speakers = list(speakers)

    async def ask_json(self, prompt):
        return {"speakers": self.speakers}


async def test_the_judge_credits_the_lines_tags_leave_uncredited(tmp_path):
    """(F) W16-1 (D16-5): pronoun-tagged lines take the judge's speakers (an
    alias resolves; one speaker's run in a paragraph is one line), so R2
    applies; an "other" or unknown speaker's line is excluded. B1: a reply of
    the wrong length credits nothing."""
    client, said = _ProductClient(), '"Is it?" he asked. "Truly?"\n\n"No," she said.'
    get = client.get_scene
    lines = f'{said}\n\n"Ice," a clerk said.\n\n"Go," he said.'
    client.get_scene = lambda sid: _with(get(sid), content=lines)
    who = ["Milo Voss", "Milo", "ms. okafor", "other", "Edric"]
    out = await pipeline.step_prose(_cel_ctx(tmp_path, client, _SpeakerJudge(*who)))
    s = out["scenes"][0]
    assert s["spoken_lines"] == [
        {"speaker": "Milo Voss", "line": "Is it? Truly?"},
        {"speaker": "Zara Okafor", "line": "No,"},
    ]
    assert s["r2_source"] == "judge" and s["rubrics"]["R2"]["score"] == 0.8
    assert (out["r2_applicable"], out["r2_lines"]) == (1.0, 4)
    out = await pipeline.step_prose(_cel_ctx(tmp_path, client, _SpeakerJudge(*who[1:])))
    assert (out["r2_applicable"], out["r2_lines"]) == (0.0, 0)


async def _with(scene, **kw):
    return {**(await scene), **kw}


async def test_prose_writes_the_planned_scenes_of_chapter_1(tmp_path):
    client = _ProductClient()
    judge = _RubricJudge()
    out = await pipeline.step_prose(_cel_ctx(tmp_path, client, judge))

    assert out["path"] == "planned" and out["prose_chapter"] == 1
    # ch-1 (Milo + Zara) is the one written, in order (W10-3 (a)).
    assert [c[1] for c in client.calls if c[0] == "generate_planned"] == ["ch-1"]
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
    assert {r["owner"] for r in out["voice"]["withheld"]} == {"Zara Okafor"}  # W9-1

    s1, s2 = out["scenes"]
    # Scene 1 repeats a sentence across its beat seam: R4 is capped at 0.
    assert s1["beat_seam_max_overlap"] >= 1
    assert s1["rubrics"]["R4"]["score"] == 0.0 and "R4" in s1["deterministic_failures"]
    # (F) W15-3: scene 1 has no cast dialogue, so R2 is not applicable (None,
    # no judge call) and its mean skips R2; scene 2 keeps its credited lines.
    assert s1["rubrics"]["R2"]["not_applicable"] and s1["spoken_lines"] == []
    assert s1["score"] == pytest.approx((0.6 + 0.0 + 0.7) / 3, abs=1e-4)
    assert sum(rid == "R2" for rid, _ in judge.seen) == 1
    # Review N4: a split quote is one line; N3: R2 graded 1 of 2 scenes.
    assert [r["line"] for r in s2["spoken_lines"]] == [
        "Is that normal? Zara, is it?",
        "No, Bring nothing.",
    ]
    assert (out["r2_applicable"], out["r2_lines"]) == (0.5, 2)
    assert s2["beat_seam_max_overlap"] == 0 and s2["deterministic_failures"] == {}
    assert s2["score"] == pytest.approx(1.0 * (0.8 + 0.6 + 0.9 + 0.7) / 4)
    assert out["score"] == pytest.approx((s1["score"] + s2["score"]) / 2, abs=1e-4)
    assert out["cast_named"] == 1.0 and out["words"] > 0
    # (F) Reported beside unknown_canon: the app's stored warning (W6-3). The
    # unplaced events are the signed file's, in prompt_contract (W7-2, D4).
    assert [s["name_warnings"] for s in out["scenes"]] == [["Gary"], []]
    assert "unplaced_events" not in out

    shown = dict(judge.seen)  # each rubric's last input: scene 2's
    # The judge sees the canon, the style guide's sample prose and the beats...
    assert "Milo initialled the form in the box marked INITIAL HERE" in shown["R3"]
    assert "PLANNED BEATS (in order):\n1. Milo signs\n2. Zara warns" in shown["R3"]
    assert "Chief Executive Lich" in shown["R1"]
    # ...and for R2, only the untagged lines plus each speaker's own samples.
    assert "said Zara" not in shown["R2"]
    assert "1. Is that normal? [name], is it?" in shown["R2"]
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


async def test_prose_is_pinned_to_chapter_1_on_the_w9b_shape(tmp_path):
    """(F) W10-3 (a): W9b's chapter 1 cast one principal, so its prose ran
    chapter 2, unreported. Chapter 1 is written whatever its cast."""
    client = _ProductClient(w9b_shape=True)
    out = await pipeline.step_prose(_cel_ctx(tmp_path, client))
    assert (out["prose_chapter"], out["chapter"]) == (1, "title ch-1")
    assert [c[1] for c in client.calls if c[0] == "generate_planned"] == ["ch-1"]


async def test_a_failed_planned_path_fails_loudly_and_is_tried_again(tmp_path):
    """M16 (SPEC-39): no fallback scene and (W10-3 (a)) no other chapter; the
    failure is reported, scored None and not memoised (S5), so the next run
    takes the planned path."""
    client = _ProductClient(planned_fails=True)
    out = await pipeline.step_prose(_cel_ctx(tmp_path, client))
    assert out["path"] == "failed" and "no planned beats" in out["reason"]
    assert [c[1] for c in client.calls if c[0] == "generate_planned"] == ["ch-1"]
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
    # (F) W15-2: R3's own body labels the chapter plan and the scenes to check.
    r3 = judge.seen[2][1]
    assert r3.index("THE PLAN") < r3.index("1. Elara arrives") < r3.index("THE TEXT")
    assert gt["premise_edit"]["premise_restated"] in judge.seen[0][1]
    assert "premise_restated" not in judge.seen[0][1]


_EMPTY = "No artifact was provided to score."
_NO_VOICE = "No dialogue provided to evaluate voice distinctness"


@pytest.mark.parametrize(
    "first, second, score",
    [
        (_EMPTY, "No text was given.", None),
        (_EMPTY, "ok", 0.7),
        # W17-2: the verb is optional.
        ("No dialogue provided", _NO_VOICE, None),
        (_NO_VOICE, "No dialogue provided", None),
        # Run 40161986625: "presented" (Dracula scene 2 scored 0.0).
        ("No dialogue was presented to evaluate voice distinctness", _NO_VOICE, None),
        # Not a claim of absent input: scored as answered, never re-asked.
        ("There is no outline deviation; every beat given is realised", "", 0.0),
        ("Text provided: no dialogue errors, beats given in order", "", 0.0),
        ("No dialogue tag was provided for line 3", "", 0.0),
    ],
)
async def test_a_judge_claiming_no_input_is_asked_again_then_unscored(
    first, second, score
):
    # (F) W15-2: one retry; a second "nothing provided" is None, never 0, and
    # an unscored R1 leaves the outline score None rather than crashing.
    replies = iter([first, second])

    class Judge:
        async def score(self, rubric, content):
            why = next(replies)
            return SimpleNamespace(score=0.7 if why == "ok" else 0.0, explanation=why)

    ctx = SimpleNamespace(judge=Judge())
    (j,) = (await pipeline._judge_rubrics(ctx, {"R1": "body"})).values()
    assert j["score"] == score
    assert j.get("judge_claimed_empty", False) == (score is None)
    assert rubrics.outline_score({"R1": j["score"], "R6": 0.5, "R3": 0.6}) == (
        None if score is None else pytest.approx(score * 0.55)
    )


async def test_outline_score_is_r1_times_mean_r6_r3(tmp_path):
    out = await pipeline.step_outline(_cel_ctx(tmp_path, client := _ProductClient()))
    assert out["cast_fidelity"] == 1.0 and out["deterministic_failures"] == {}
    # (F) W10-1 (a): the excerpt is the outline book's source before its outline.
    calls = [c[0] for c in client.calls]
    assert (
        calls.index("commit") < calls.index("outline") and out["source"] == "ingested"
    )
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
        "Prava, the intake caseworker who onboarded Milo with such practiced warmth"
    )  # (F) SPEC-57: from the name, no "There is"
    assert not any(c["description"].startswith("There ") for c in minor.values())
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
    """(F) W6-7 (c), D2: the longest beat-1 paragraph of >= 30 words first, so
    the redo meets beat 2 as written (else the scene's longest), then the
    longest of the rest; distinct, never repeated; none for W5b's five-word
    target. B-4: no keep clause."""
    long, longer = " ".join(["He read the ledger."] * 8), " ".join(["Zara."] * 40)
    draft = f"{long}\n\nShort.\n\n{longer}"
    beats = [{"content": f"{long}\n\nShort."}, {"content": longer}]
    for bs, texts in (
        (beats, [long, longer]),
        ([{"content": "Short."}], [longer, long]),
        ([], [longer, long]),
    ):
        reqs = pipeline.tighten_requests(draft, bs)
        assert [draft[r["range"]["start"] : r["range"]["end"]] for r in reqs] == texts
        assert [r["id"] for r in reqs] == ["tighten", "tighten_2"]
        assert {r["kind"] for r in reqs} == {"tighten"}
    assert pipeline.tighten_requests("Short.\n\nAlso short.", []) == []
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
    # Wave-8 day-0: the report's kind and count; the API body has no kind.
    assert [r.get("kind") for r in out["revisions"]] == ["tighten", None, None]
    assert out["tighten_n"] == 1
    # W8-1 (c): cut and kept shares, by the grader's own stems, and the RAs.
    assert (tighten["tighten_cut"], tighten["content_kept"]) == (0.2, 0.81)
    assert out["tighten_ras"] == [tighten["rubrics"]["RA"]["score"]]
    assert not any("kind" in body for body in client.revised)
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


LONG_2 = " ".join(["Milo counted the chairs and found one more each time."] * 4)
LONG_3 = " ".join(["The ledger ran to the floor."] * 6)


@pytest.mark.parametrize("extra", [[LONG_2, LONG_3]])
async def test_revise_step_requests_three_tightens_and_accepts_the_first(
    tmp_path, extra
):
    """(F) D2: up to three distinct paragraphs; the first is accepted and the
    others are asked of the accepted draft, then discarded."""
    client = _ReviseClient()
    client.draft = "\n\n".join([REVISE_DRAFT, *extra])
    ctx = _cel_ctx(tmp_path, client, _RubricJudge())
    await ctx.client.create_book("x", "s")
    out = await pipeline.step_revise(ctx)
    tightens = [r for r in out["revisions"] if r.get("kind") == "tighten"]
    accepted = client.proposals["p1"]["content"]
    assert tightens[0]["accepted"] is True and len(tightens) == 1 + len(extra)
    assert [r["draft"] for r in client.revised[: len(tightens)]] == [
        client.revised[0]["draft"],
        *[accepted] * len(extra),
    ]
    asked = [b["range"] for b in client.revised[1 : len(tightens)]]
    longest = sorted(extra, key=len, reverse=True)  # the rest, longest first
    assert [accepted[a["start"] : a["end"]] for a in asked] == longest
    assert client.discarded[: len(extra)] == [f"p{i + 2}" for i in range(len(extra))]
    assert not any("kind" in body for body in client.revised)
    assert out["tighten_n"] == len(tightens)


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


def test_the_withheld_export_is_built_from_the_grounding_body():
    """(F) W9-1 (b): each character group holding `withheld` lines becomes one
    `voice.withheld` row under its beat's description (SPEC-69)."""
    held = [{"chunk_id": "c", "rule": "restates", "text": "p2", "excerpt": "p"}]
    zara = {"name": "Zara Okafor", "samples": [], "withheld": held, "withheld_more": 3}
    milo = {"name": "Milo Voss", "samples": [{"chunk_id": "m", "text": "m"}]}
    body = {"beats": [{"description": "Beat one.", "characters": [zara, milo]}]}
    body["beats"].append({"description": "Beat two.", "characters": []})
    assert pipeline.withheld_rows(body) == [
        {"beat": "Beat one.", "owner": "Zara Okafor"}
        | {"lines": [{"text": "p2", "rule": "restates"}], "withheld_more": 3}
    ]


def test_dracula_vasska_speech_lines_feed_r2_only():
    # (F) W17-3: the Count's own words, verbatim, read only by R2's samples.
    d = pipeline.CORPORA / "dracula"
    gt = json.loads((d / "ground_truth.json").read_text(encoding="utf-8"))
    text = re.sub(r"\s+", " ", (d / "excerpts.txt").read_text(encoding="utf-8"))
    v = gt["speech_lines"]["Count Vasska"]
    lines = v["train"] + v["test"]
    assert len(lines) >= 6 and all(ln in text for ln in lines)
    assert "Count Vasska" not in gt["gold_lines"]
    block = pipeline._voice_block(gt, ["Aldous Kerr", "Count Vasska"])
    assert "Count Vasska:" in block and "Aldous Kerr:" in block


# --- W18-6/7 (SPEC-93): RN, the narrator; RC, the chapter as a whole -----------


def test_rn_and_rc_rubrics_are_anchored_and_outside_the_scores():
    for rid in rubrics.BOOK_RUBRICS:
        text = rubrics.rubric(rid)
        assert text.startswith(f"### {rid}. ")
        for anchor in ("**1.0**", "**0.7**", "**0.4**", "**0.0**"):
            assert anchor in text, (rid, anchor)
    assert "NARRATOR SAMPLES" in rubrics.rubric("RN")
    assert "POV" in rubrics.rubric("RN") and "tense" in rubrics.rubric("RN")
    assert "PLAN" in rubrics.rubric("RC") and "repeated" in rubrics.rubric("RC")
    assert not set(rubrics.BOOK_RUBRICS) & set(rubrics.SCENE_RUBRICS)
    base = {"R1": 1.0, "R2": 0.8, "R3": 0.6, "R4": 0.9, "R5": 0.7}
    assert rubrics.scene_score(base | {"RN": 0.0}) == rubrics.scene_score(base)


@pytest.mark.parametrize("book", ["cel", "dracula", "frankenstein", "wuthering"])
def test_narrator_samples_are_a_fixed_set_of_the_sources_narration(book):
    got, who = pipeline.narrator_samples(book)
    assert who == "corpus" and len(got) == 3
    assert (got, who) == pipeline.narrator_samples(book)
    flat = " ".join(pipeline.load_excerpts(book).split()).replace("\u2019", "'")
    for s in got:
        assert not re.search('["\u201c\u201d]', s)
        assert 20 <= len(s.split()) <= 150 and not s.endswith(" \u2026")
        assert " ".join(s.split()[:12]) in flat
    # B1: the spread reaches the excerpt's last narration paragraph.
    assert flat.rindex(" ".join(got[-1].split()[:12])) > len(flat) * 2 // 3
    assert pipeline.narrator_samples("no-such-corpus") == ([], "corpus")


def test_narrator_samples_follow_the_chapter_pov():
    """B1: a POV with `voice_sources` chapters samples that narrator only."""
    text = pipeline.load_excerpts("frankenstein").replace("\u2019", "'")
    flat = " ".join(text.split())
    letters = flat[: flat.index("Chapter 5")]
    emeric, who = pipeline.narrator_samples("frankenstein", "Emeric Vandel")
    assert who == "Emeric Vandel" and len(emeric) == 3
    rowan, who = pipeline.narrator_samples("frankenstein", "Rowan Merrow")
    assert who == "Rowan Merrow" and len(rowan) == 3
    assert all(" ".join(s.split()[:12]) in letters for s in rowan)
    assert not any(" ".join(s.split()[:12]) in letters for s in emeric)
    assert pipeline.narrator_samples("frankenstein", "Nobody")[1] == "corpus"
    assert pipeline.narrator_samples("cel", "Milo Voss")[1] == "corpus"


def test_scene_narration_removes_the_dialogue_and_its_tags():
    text = (
        'Zara set down her pen and looked at the queue for a long time. "Read it,'
        ' Milo," said Zara Okafor.\n\n"Is that normal?" Milo asked.\n\n'
        '"Why would it be, after all this time here?" she asked, low.\n\n'
        "He laughed."
    )
    got = pipeline.scene_narration(text)
    assert got == (
        "Zara set down her pen and looked at the queue for a long time.\n\n"
        "He laughed."
    )  # B2: no tag fragment, and a mostly-dialogue paragraph is left out


def test_scene_narration_removes_single_quoted_dialogue_too():
    assert pipeline.scene_narration("'You must go now,' Milo said.") == ""
    got = pipeline.scene_narration(
        "The boys' coats hung by the fire. It wasn't late, and the hall was dark."
        " \u2018Go,\u2019 she said."
    )
    assert got == (
        "The boys' coats hung by the fire. It wasn't late, and the hall was dark."
    )  # Codex P2: single quotes are speech; apostrophes stay narration


def test_rc_trims_each_scene_middle_when_the_chapter_is_long():
    long = [" ".join(f"s{n}w{i}" for i in range(2000)) for n in range(2)]
    text, trimmed = pipeline.rc_content({"title": "T", "summary": "S"}, long, 1000)
    assert trimmed and text.split("[Scene 1]")[1].count(" […] ") == 2
    assert '"[…]" marks each cut' in text and "trimmed in the middle" in text
    assert "s0w0 " in text and "s0w1999" in text and "s0w1000" not in text
    assert len(text.split()) < 1100
    short, trimmed = pipeline.rc_content({"title": "T"}, ["a b", "c d"], 1000)
    assert not trimmed and "[…]" not in short


def test_rc_plan_lists_the_planned_beat_titles():
    plan = {"title": "T", "summary": "S", "planned_beats": [{"title": "Intake"}]}
    text, _ = pipeline.rc_content(plan, ["a b"])
    assert text.startswith(
        "PLAN (the outline's chapter):\nCHAPTER: T\nS\n"
        "PLANNED BEATS (in order):\n1. Intake\n\nCHAPTER TO SCORE"
    )


class _BookJudge(_RubricJudge):
    def __init__(self, rn, rc):
        super().__init__()
        self.rn, self.rc = rn, rc

    async def score(self, rubric, content):
        got = await super().score(rubric, content)
        rid = self.seen[-1][0]
        return SimpleNamespace(
            score={"RN": self.rn, "RC": self.rc}.get(rid, got.score),
            explanation=got.explanation,
        )


async def test_prose_reports_rn_and_rc_as_a_book_block(tmp_path):
    judge = _RubricJudge()
    out = await pipeline.step_prose(_cel_ctx(tmp_path, _ProductClient(), judge))
    s1, s2 = out["scenes"]
    # RN: scene 1 is narration, judged beside the cel narrator; scene 2 is all
    # dialogue and speech tags, so RN is not applicable there.
    (rn,) = [c for rid, c in judge.seen if rid == "RN"]
    assert rn.startswith(
        "THE BOOK'S CONFIGURED NARRATION: pov: third person limited, on Milo"
    )
    assert "\n\nNARRATOR SAMPLES (the source's own narration" in rn
    assert "its dialogue and the speech tags beside it were removed" in rn
    for sample in pipeline.narrator_samples("cel")[0]:
        assert sample in rn
    assert rn.split("TEXT TO SCORE")[1].count("Milo initialled the form") == 1
    assert s1["rubrics"]["RN"]["score"] == 0.3
    assert s2["rubrics"]["RN"]["not_applicable"]
    # RC: one call, the whole chapter in order under the outline chapter's plan.
    (rc,) = [c for rid, c in judge.seen if rid == "RC"]
    assert rc.startswith("PLAN (the outline's chapter):\nCHAPTER: title ch-1")
    one, two = rc.index("[Scene 1]\n" + SCENE_1), rc.index("[Scene 2]\n" + SCENE_2)
    assert one < two and "[…]" not in rc
    assert out["book"] == {
        "rn_mean": 0.3,
        "rc": 0.2,
        "rc_explanation": "RC ok",
        "rc_trimmed": False,
        "narrator_samples": 3,
        "narrator_sampled": "corpus",  # Milo has no voice_sources chapters
    }
    assert "RN" not in out["rubric_means"] and "RN" not in out["judge_explanation"]
    # D18-4: the prose score is the same whatever RN and RC say.
    for rn_score, rc_score in ((0.0, 0.0), (1.0, 1.0)):
        again = await pipeline.step_prose(
            _cel_ctx(tmp_path, _ProductClient(), _BookJudge(rn_score, rc_score))
        )
        assert again["score"] == out["score"]
        assert [s["score"] for s in again["scenes"]] == [s1["score"], s2["score"]]
        assert again["book"]["rc"] == rc_score


def test_the_book_block_is_exported_as_its_own_metrics():
    from evals.harness import report

    book = {"rn_mean": 0.55, "rc": 0.4, "rc_explanation": "x", "rc_trimmed": True}
    run = {
        "app_sha": "s",
        "book": "cel",
        "judge": {"provider": "p", "self": False},
        "steps": {"prose": {"score": 0.7, "words": 400, "book": book}},
    }
    out = report.tracer_export(run, model="m")
    (case,) = out["cases"]
    assert case["score"] == 0.7
    assert (case["metrics"]["rn_mean"], case["metrics"]["rc"]) == (0.55, 0.4)
    assert (out["metrics"]["prose.rn_mean"], out["metrics"]["prose.rc"]) == (0.55, 0.4)
    gl = report.greenlight_export(run)
    assert [c["name"] for c in gl["checks"]] == ["eval:prose"]
    assert gl["checks"][0]["eval"]["score"] == 0.7
    assert gl["metrics"] == {"prose.rn_mean": 0.55, "prose.rc": 0.4}
    assert "RN=0.55 RC=0.4 (chapter trimmed)" in report.scorecard(run)
    del run["steps"]["prose"]["book"]
    assert "metrics" not in report.greenlight_export(run)


def test_repeats_average_the_book_block():
    from evals.run import _aggregate

    passes = [
        {"prose": {"score": 0.6, "book": {"rn_mean": 0.4, "rc": 0.2}}},
        {"prose": {"score": 0.8, "book": {"rn_mean": 0.6, "rc": None}}},
    ]
    got = _aggregate(passes)["prose"]
    assert got["score"] == 0.7 and got["book"] == {"rn_mean": 0.5, "rc": 0.2}


# --- SPEC-94 S2b/S2c through the continuity step ------------------------------


def _continuity_client(injected_findings, control_findings):
    class Client(_ProductClient):
        async def create_book(self, title, synopsis, genre=""):
            self.ids[title] = {}
            return {"id": title}

        async def create_chapter(self, book_id, title, summary=""):
            return {"id": book_id}

        async def create_scene(self, chapter_id, content="", characters=None):
            pass

        async def run_continuity(self, book_id, chapter_id=None):
            return injected_findings if "injected" in book_id else control_findings

    return Client()


async def test_continuity_step_credits_only_findings_on_the_injected_span(tmp_path):
    # "Edric Thane" stands all over the cel excerpt; the memo's injected
    # "Edwin Thane" is the one planted error. A finding quoting another
    # "Edric Thane" merely mentions a token (old: detected, precision 1.0).
    elsewhere = [{"detail": "Edwin Thane vs Edric Thane", "quote": "Edric Thane"}]
    client = _continuity_client(elsewhere, [])
    out = await pipeline.step_continuity(_cel_ctx(tmp_path, client))
    assert out["detected"] == [] and out["precision"] == 0.0
    on_span = [{"detail": "signature", "quote": "Edwin Thane"}]
    client = _continuity_client(on_span, [])
    out = await pipeline.step_continuity(_cel_ctx(tmp_path, client))
    assert out["detected"] == ["broken-alias"] and out["precision"] == 1.0


async def test_continuity_step_reports_false_positives_per_1000_control_words(
    tmp_path,
):
    found = [{"detail": "a"}, {"detail": "b"}, {"detail": "c"}]
    out = await pipeline.step_continuity(
        _cel_ctx(tmp_path, _continuity_client([], found))
    )
    # Old: false_positive_rate = min(1, 3 control findings / 5 injected) = 0.6.
    assert "false_positive_rate" not in out
    words = len(pipeline.load_excerpts("cel")[: pipeline._CONTINUITY_WINDOW].split())
    assert out["n_control_words"] == words
    assert out["control_fp_per_1k_words"] == round(3000 / words, 4)
    assert out["control_flagged_share"] == 1.0


@pytest.mark.parametrize("book", ["cel", "dracula", "frankenstein", "wuthering"])
def test_every_corpus_injection_has_a_span_on_its_changed_words(book):
    from evals.graders import continuity

    text, gt = pipeline.load_corpus(book)
    if gt.get("continuity_source") == "excerpts":
        text = pipeline.load_excerpts(book)
    window = text[: pipeline._CONTINUITY_WINDOW]
    injected, expected = continuity.apply_injections(
        window, gt["continuity_injections"]
    )
    # (Wuthering's name-misspell anchor lies past the window, so it is not
    # applied: a gap that predates S2 and is not its to fix.)
    assert expected
    for e in expected:
        a, b = e["span"]
        assert injected[a:b] == e["injected"] and e["injected"].strip(), e["id"]
        # The span is the changed part of the replacement, never its anchor.
        assert e["injected"] in e["replace"] and len(e["injected"]) <= len(e["replace"])
        # A finding quoting exactly the changed words catches it.
        hit = {"detail": "x", "quote": e["injected"]}
        assert continuity._finding_matches(e, hit, injected), e["id"]
