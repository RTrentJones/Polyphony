"""Unit tests for the eval steps' contract with the app — the defects seen in
the weekly live run:

(a) extraction scored 0: the step graded the source's committed cast, but
    extraction only PROPOSES (nothing is on the source until review/commit), so
    it read an empty list. It must grade the run's `proposals.characters`. And
    the cel corpus uploaded a 389-char excerpt that never names its cast.
(b) attribution got HTTP 422: characters are book-scoped and the step created
    them without a book_id.
"""

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
    gt = {
        "synopsis": "s",
        "gold_lines": {
            "Nora": {"train": ["a"], "test": ["b"]},
            "Elias": {"train": ["c"], "test": ["d"]},
        },
    }
    client = _FakeClient({})
    out = await pipeline.step_attribution(_ctx(tmp_path, client, gt))
    assert client.created == [("Nora", "book-2"), ("Elias", "book-2")]
    assert out["generated"] == 2 and "error" not in out


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
