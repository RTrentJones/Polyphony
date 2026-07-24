"""Unit tests for the book domain: exports, prose call budget, planning."""

from unittest.mock import AsyncMock, patch

import pytest

from app.exports.builder import (
    BookExport,
    ChapterExport,
    SceneExport,
    to_docx,
    to_epub,
    to_markdown,
)
from app.planning.continuity import chunk_prose, validate_findings
from app.planning.outline import validate_outline_nodes

BOOK = BookExport(
    title="The Test Book",
    author="A. Writer",
    synopsis="A book about tests.",
    chapters=[
        ChapterExport(
            title="Beginnings",
            scenes=[
                SceneExport(
                    title="",
                    content="It was a dark and stormy night.\n\nThen it wasn't.",
                ),
            ],
        ),
        ChapterExport(
            title="Endings",
            scenes=[SceneExport(title="", content="The end came quickly.")],
        ),
    ],
)


@pytest.mark.unit
class TestExports:
    def test_markdown_contains_structure(self):
        md = to_markdown(BOOK)
        assert "# The Test Book" in md
        assert "## Chapter 1: Beginnings" in md
        assert "## Chapter 2: Endings" in md
        assert "dark and stormy" in md

    def test_docx_round_trip(self):
        import io

        from docx import Document

        data = to_docx(BOOK)
        doc = Document(io.BytesIO(data))
        text = "\n".join(p.text for p in doc.paragraphs)
        assert "The Test Book" in text
        assert "The end came quickly." in text

    def test_epub_is_valid_zip_with_chapters(self):
        import zipfile
        import io

        data = to_epub(BOOK)
        archive = zipfile.ZipFile(io.BytesIO(data))
        names = archive.namelist()
        assert "mimetype" in names
        assert any("chapter_1" in n for n in names)
        assert any("chapter_2" in n for n in names)


@pytest.mark.unit
class TestProseCallBudget:
    @pytest.mark.asyncio
    async def test_four_beat_scene_costs_at_most_six_calls(self):
        """Regression guard: prose mode must stay at 1 planning + 1 call/beat."""
        from app.orchestration import prose as prose_module

        calls = {"n": 0}

        class FakeResult:
            text = "1. beat one\n2. beat two\n3. beat three\n4. beat four"
            tokens_in = 10
            tokens_out = 10

        class FakeClient:
            provider = type("P", (), {"id": "fake"})()

            async def generate(self, *args, **kwargs):
                calls["n"] += 1
                return FakeResult()

        scene_request = {
            "source_id": None,
            "characters": ["Alice", "Bob"],
            "scene_description": "A tense negotiation over the last biscuit.",
            "setting": "kitchen",
            "emotional_tone": "tense",
            "target_word_count": 800,
        }

        with (
            patch.object(prose_module, "get_llm_client", lambda: FakeClient()),
            patch("app.orchestration.workflow.get_llm_client", lambda: FakeClient()),
            patch.object(
                prose_module, "build_cast_context", AsyncMock(return_value="cast")
            ),
        ):
            beats = await prose_module.plan_scene_beats(scene_request, None)
            assert len(beats) == 4
            for beat in beats:
                await prose_module.write_beat_prose(
                    beat, scene_request, "cast", "", user_id=None
                )

        # 1 planning call + 4 beat calls = 5 <= 6
        assert calls["n"] <= 6


@pytest.mark.unit
class TestOutlineValidation:
    def test_normalizes_nested_nodes(self):
        nodes = validate_outline_nodes(
            [
                {
                    "title": "Ch 1",
                    "summary": "opening",
                    "children": [{"title": "beat", "summary": ""}],
                },
                {"summary": "no title -> dropped"},
                "garbage",
            ]
        )
        assert len(nodes) == 1
        assert nodes[0]["title"] == "Ch 1"
        assert nodes[0]["children"][0]["title"] == "beat"

    def test_rejects_non_list(self):
        with pytest.raises(ValueError):
            validate_outline_nodes({"title": "not a list"})


@pytest.mark.unit
class TestContinuityHelpers:
    def test_chunking_covers_all_words(self):
        text = " ".join(f"w{i}" for i in range(6000))
        chunks = chunk_prose(text, chunk_words=2500)
        assert len(chunks) == 3
        assert sum(len(c.split()) for c in chunks) == 6000

    def test_findings_validation_filters_garbage(self):
        findings = validate_findings(
            [
                {"type": "TIMELINE", "severity": "Major", "detail": "day repeats"},
                {"type": "weird", "detail": "still kept as other"},
                {"detail": ""},
                "not a dict",
            ]
        )
        assert len(findings) == 2
        assert findings[0]["type"] == "timeline"
        assert findings[1]["type"] == "other"
