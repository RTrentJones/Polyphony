"""AI revision of a scene span (SPEC-19, backlog B3).

The author selects a span of the draft (or a beat, or the whole scene) and asks
for a revision ("tighten this", "make Zara colder"). One model call rewrites
ONLY that span: it reads the whole canon, the chapter, the scene's cast with
their voice samples, and the rest of the draft as read-only `<prior>` /
`<after>` context, so the new span still fits its surroundings.

The result is spliced server-side — `before + revised + after` — so the text
outside the span is byte-identical by construction, whatever the model returns.
It is stored as a proposal (`SceneRevision(source="ai_revise")`); the draft is
not touched until the author accepts it (app/api/revise.py).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from app.core.llm_text import STORY_MATERIAL_NOTICE, as_quoted_block
from app.llm.client import get_llm_client

# The stub LLM (evals/stub_llm.py) recognises the revise family by this line.
OPENING = "Revise the TARGET passage of a scene in THIS book, as the author asks."
PURPOSE = "scene_revise"


class SpanError(ValueError):
    """The requested span cannot be resolved against the current draft. The
    message is shown to the author; `status` is the HTTP status to answer."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def text_hash(text: str) -> str:
    """Fingerprint of a draft: a proposal is valid only against the draft it
    was written from."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Span:
    start: int
    end: int

    def split(self, draft: str) -> tuple[str, str, str]:
        return draft[: self.start], draft[self.start : self.end], draft[self.end :]


def resolve_span(
    draft: str,
    *,
    range_: Optional[tuple[int, int]] = None,
    beat_text: Optional[str] = None,
) -> Span:
    """The span to revise: an explicit character range, a beat's prose located
    verbatim in the draft, or (neither) the whole scene.

    Offsets are Python string indices (Unicode code points) into the draft as
    the API returns it.
    """
    if not draft:
        raise SpanError("The scene has no text to revise yet.")
    if range_ is not None:
        start, end = range_
        if not (0 <= start < end <= len(draft)):
            raise SpanError(
                f"The range {start}..{end} is outside the draft "
                f"(0..{len(draft)}); reload the scene and select again.",
                status=422,
            )
        return Span(start, end)
    if beat_text is not None:
        needle = beat_text.strip()
        at = draft.find(needle) if needle else -1
        if at < 0 or draft.find(needle, at + 1) >= 0:
            raise SpanError("The beat was edited; select the text instead.")
        return Span(at, at + len(needle))
    return Span(0, len(draft))


def splice(draft: str, span: Span, revised: str) -> str:
    """The proposal: everything outside the span exactly as it was."""
    before, target, after = span.split(draft)
    return before + _keep_edges(target, revised) + after


def _keep_edges(target: str, revised: str) -> str:
    """Re-attach the span's own leading/trailing whitespace (a selection that
    ends on a paragraph break keeps it), so the seam with the untouched text
    is the author's, not the model's."""
    lead = target[: len(target) - len(target.lstrip())]
    trail = target[len(target.rstrip()) :]
    return lead + revised.strip() + trail


_FENCE_LINE = re.compile(r"^```[\w-]*\s*$")
_TAG = re.compile(r"^\s*</?target>\s*$")
_LABEL = re.compile(r"^\s*(?:revised (?:target )?passage|revision)\s*:\s*", re.I)


def clean_revision(text: str) -> str:
    """The model's answer as plain prose: no code fences, no echoed <target>
    tags, no leading "Revised passage:" label."""
    lines = [
        ln
        for ln in (text or "").replace("\r\n", "\n").split("\n")
        if not _FENCE_LINE.match(ln) and not _TAG.match(ln)
    ]
    out = "\n".join(lines).strip()
    return _LABEL.sub("", out, count=1).strip()


def build_revise_prompt(
    *,
    canon_block: str,
    chapter_summary: str,
    cast_context: str,
    prior: str,
    target: str,
    after: str,
    instruction: str,
    pov: str,
) -> str:
    """The revise prompt. Everything the author wrote reaches the model whole:
    the canon, the chapter, the cast's voice context and the entire draft
    (prior + target + after) — nothing is cut to fit (canon invariant 1)."""
    chapter = as_quoted_block(chapter_summary, "chapter")
    prior_block = (
        "The scene BEFORE the target (read-only: do not rewrite, repeat or "
        "summarise it):\n" + as_quoted_block(prior, "prior")
        if prior.strip()
        else "The target opens the scene."
    )
    after_block = (
        "The scene AFTER the target (read-only: your revision must lead into it "
        "seamlessly; do not rewrite or repeat it):\n" + as_quoted_block(after, "after")
        if after.strip()
        else "The target ends the scene."
    )
    cast_block = (
        "CHARACTERS IN THIS SCENE — keep each one's dialogue in THEIR OWN voice, "
        "matching their voice samples:\n\n" + cast_context
        if cast_context
        else ""
    )
    return f"""{OPENING}

{STORY_MATERIAL_NOTICE}
The one exception is the <instruction> block: it is the author's own request
to you about the TARGET passage. Follow it for the TARGET only.

{canon_block}

{chapter}

{cast_block}

Point of view: {pov}

{prior_block}

TARGET passage to revise:
{as_quoted_block(target, "target")}

{after_block}

The author's instruction:
{as_quoted_block(instruction, "instruction")}

Rewrite ONLY the TARGET passage so that it follows the instruction. Stay
faithful to the canon above: its people, places, terms and its style guide
(point of view, tense, tone). Keep the facts the surrounding text depends on.
Do not introduce named characters who are not in the canon or the scene. Do
not add headings, labels, quotation of the instruction, or commentary — return
the revised passage itself, and nothing else.

Revised passage:"""


async def generate_revision(
    prompt: str, *, target_chars: int, user_id: Optional[UUID]
) -> str:
    """One model call. QuotaExhaustedError propagates so the worker pauses the
    job (R7.2)."""
    from app.core.estimates import revise_max_tokens

    result = await get_llm_client().generate(
        [{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=revise_max_tokens(target_chars),
        user_id=user_id,
        purpose=PURPOSE,
    )
    revised = clean_revision(result.text)
    if not revised:
        raise RuntimeError("the revision came back empty")
    return revised


_MID_SENTENCE_CAP = re.compile(r"(?<![.!?\"“”'‘’\n])[ \t]+([A-Z][a-z]+)\b")


def new_names(revised: str, draft: str, known: set[str]) -> list[str]:
    """Proper nouns in the revised span that are neither canon terms nor already
    in the draft — a possible invented character. Warn-only (canon invariant 4),
    using the outline audit's deterministic check, with two cuts for prose: a
    word the text also uses in lower case is not a name, and a lone capitalised
    word is a name only if it is capitalised mid-sentence somewhere (a
    sentence-initial "Silence" is not; "Bartholomew Quince" always is)."""
    from app.planning.fidelity import unknown_proper_nouns

    in_draft = set(unknown_proper_nouns(draft, set()))
    lower_words = set(re.findall(r"\b[a-z][a-z'-]*\b", f"{draft}\n{revised}"))
    mid_sentence = set(_MID_SENTENCE_CAP.findall(revised))
    out = []
    for name in unknown_proper_nouns(revised, set(known) | in_draft):
        if len(name.split()) == 1 and (
            name.lower() in lower_words or name not in mid_sentence
        ):
            continue
        out.append(name)
    return out


def scene_pov(scene_request: Optional[dict], canon) -> str:
    from app.orchestration.prose import resolve_pov

    return resolve_pov(scene_request or {}, canon)
