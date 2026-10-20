"""A deterministic, OpenAI-compatible stub LLM for end-to-end testing.

Boot the real app against this (``LLM_PROVIDER=stub``) to drive every flow —
upload → extraction → review → outline → scenes → continuity — with no API key,
no quota and no cost. It exists for SPEC-06 (eval parity) and for local
end-to-end testing of the website.

It is deliberately *honest about its input*: every answer is built only from
what the prompt actually contains. Character names come from the canon blocks
(``### Name`` headings, or the synopsis), so if the input pipeline stops
delivering the cast to the model, the stub's outline stops naming the cast and
the fidelity gate fails — exactly as a real model would. That is what makes it
useful as a regression test of the *pipeline*, not of model quality.

Control surface (for tests):

- ``POST /__stub/config`` ``{"fail_429": N, "retry_after": S}`` — answer the
  next N requests with HTTP 429 (quota exhaustion); ``{"ensemble_rounds": R}``
  makes each beat's editor review unsatisfied until its R-th (W13-3 dry run).
- ``GET /__stub/requests`` — the most recent prompts received (newest last).
- ``POST /__stub/reset`` — clear counters and the request log.

Run: ``python -m evals.stub_llm --port 8099``
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter, deque

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.prompt_markers import (
    ACT_CHAPTERS_HEADER,
    BEAT_TO_WRITE_LABEL,
    CURRENT_BEAT_MARK,
    NAME_HEADING,
)

app = FastAPI(title="polyphony-stub-llm")

_STATE: dict = {"fail_429": 0, "retry_after": 1, "calls": 0, "ensemble_rounds": 1}
_REVIEWS: Counter = Counter()  # editor reviews per beat (ensemble_rounds)
_LOG: deque = deque(maxlen=200)

_HEADING = re.compile(rf"^{re.escape(NAME_HEADING.strip())}\s+(.+?)\s*$", re.M)
_CAP_NAME = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b")
_FENCE = re.compile(r"<(\w+)>\n(.*?)\n</\1>", re.S)
_STOP_FIRST = {
    "The",
    "A",
    "An",
    "In",
    "On",
    "At",
    "When",
    "Then",
    "But",
    "And",
    "Chapter",
    "Act",
    "Part",
    "Book",
    "Return",
    "Treat",
    "Text",
    "If",
    "You",
    "Each",
    "For",
    "Keep",
    "Write",
    "Do",
}


def _blocks(prompt: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for tag, body in _FENCE.findall(prompt):
        out[tag] = (out.get(tag, "") + "\n" + body).strip()
    return out


def _evidence(name: str, source: str) -> list[str]:
    """Map evidence as a real model should give it (W7-4): the part's own
    sentences naming them, else its first sentence."""
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", source) if x.strip()]
    hits = [x for x in sentences if name.split()[0] in x]
    return (hits or sentences)[:2]


def _cast(prompt: str) -> list[str]:
    """Character names the prompt actually carries, most-specific source first."""
    blocks = _blocks(prompt)
    canon = "\n".join(blocks.get(k, "") for k in ("canon", "characters", "character"))
    names: list[str] = []
    if canon:
        # Only headings under a "# Characters" section (or a bare character
        # block) are people; canon-entry headings are places/things.
        section = canon
        m = re.search(r"#\s*Characters\s*\n(.*?)(?:\n#\s+\w|\Z)", canon, re.S)
        if m:
            section = m.group(1)
        names = [n for n in _HEADING.findall(section) if n]
    if not names:
        m = re.search(r"Characters(?: present)?(?: in this beat)?:\s*(.+)", prompt)
        if m:
            names = [n.strip() for n in m.group(1).split(",") if n.strip()]
    if not names:
        text = blocks.get("synopsis", "") or blocks.get("source", "")
        counts = Counter(
            n for n in _CAP_NAME.findall(text) if n.split()[0] not in _STOP_FIRST
        )
        # A full name introduced once and then shortened ("Milo Voss" … "Milo")
        # is still a character: credit a full name with its first-token mentions.
        firsts = Counter(w for w in re.findall(r"\b[A-Z][a-z]+\b", text))
        scored = {n: c + firsts.get(n.split()[0], 0) for n, c in counts.items()}
        names = [n for n, c in sorted(scored.items(), key=lambda kv: -kv[1]) if c >= 3][
            :8
        ]
    seen: list[str] = []
    for n in names:
        if n not in seen:
            seen.append(n)
    return seen


_NARRATOR_HEAD = re.compile(
    r"^[\s_*]*([A-Za-z][\w.]*(?:[ \t]+[A-Za-z][\w.]*){0,3})[’']s[ \t]+"
    r"(?:journal|diary)\b",
    re.I | re.M,
)


def _narrators(prompt: str) -> list[str]:
    """First-person narrators the source block's own headings name ("Jonathan
    Harker's Journal") — what a model reading the window would report."""
    src = _blocks(prompt).get("source", "")
    out: list[str] = []
    for m in _NARRATOR_HEAD.finditer(src):
        name = " ".join(w if not w.isupper() else w.title() for w in m.group(1).split())
        if name not in out:
            out.append(name)
    return out


_APPOSITIVE = re.compile(r"\b([A-Z][a-z]+), the ")
_ACT_BEFORE, _ACT_AFTER = ACT_CHAPTERS_HEADER.split("{number}")
_ACT_CHAPTERS = re.compile(
    rf"{re.escape(_ACT_BEFORE)}(\d+){re.escape(_ACT_AFTER)}\n(.*?)\n\nFor EACH chapter",
    re.S,
)


def _named_people(prompt: str, cast: list[str]) -> list[str]:
    """People the <synopsis> introduces by apposition ("Prava, the intake
    caseworker") who are not cast: what a model would put in a beat's
    `people` (SPEC-24 §1e). The server, not the stub, decides who is secondary."""
    tokens = {t for c in cast for t in c.split()}
    out: list[str] = []
    for name in _APPOSITIVE.findall(_blocks(prompt).get("synopsis", "")):
        if name not in tokens and name not in out:
            out.append(name)
    return out


def _act_chapters(prompt: str) -> tuple[int, list[dict]]:
    """(act number, the act's chapters) as the beats prompt carries them."""
    m = _ACT_CHAPTERS.search(prompt)
    if not m:
        return 0, []
    try:
        chapters = json.loads(m.group(2))
    except ValueError:
        return int(m.group(1)), []
    return int(m.group(1)), [c for c in chapters if isinstance(c, dict)]


def _revise(prompt: str) -> str:
    """The revise family (SPEC-19): the <target> passage with its first
    sentence dropped (its last word, if it is one sentence). Derived only from
    the target the prompt carries, so a pipeline that stops sending the span
    stops getting a revision of it."""
    target = _blocks(prompt).get("target", "")
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", target.strip()) if s]
    if len(sentences) > 1:
        return " ".join(sentences[1:])
    words = target.split()
    return " ".join(words[:-1]) if len(words) > 1 else target


def _answer(prompt: str) -> str:  # noqa: C901 - one branch per prompt family
    if prompt.lstrip().startswith("Revise the TARGET passage"):
        return _revise(prompt)
    cast = _cast(prompt)
    lead = cast[0] if cast else "the protagonist"
    second = cast[1] if len(cast) > 1 else lead

    if '"premise_restated"' in prompt:
        return json.dumps(
            {
                "premise_restated": f"A story about {', '.join(cast) or lead}.",
                "central_conflict": f"{lead} against {cast[-1] if cast else 'fate'}.",
                "acts": [
                    {
                        "title": f"Act {i + 1}",
                        "goal": "advance",
                        "turn": "turn",
                        "chapters_planned": 2,
                    }
                    for i in range(3)
                ],
            }
        )
    if "Break the acts into chapters" in prompt:
        chapters = []
        for i in range(6):
            who = cast[i % len(cast)] if cast else lead
            chapters.append(
                {
                    "title": f"Chapter {i + 1}: {who}, part {i + 1}",
                    "summary": f"{who} changes course.",
                    "act_index": i // 2,
                    "pov": who,
                    "people": cast[:3],
                    "threads": [],
                }
            )
        return json.dumps(chapters)
    if "scene beats" in prompt and "mirroring the chapters" in prompt:
        # Each beat carries its own people (the chapter's), setting and tone;
        # the first chapter's first beat also names the synopsis' non-cast
        # people, so the outline offers "Add <name> to cast" (SPEC-24).
        act, chapters = _act_chapters(prompt)
        titles = [str(c.get("title") or "") for c in chapters if c.get("title")]
        if not titles:
            titles = re.findall(r"'title':\s*'([^']*)'", prompt) or re.findall(
                r'"title":\s*"([^"]*)"', prompt
            )
        named = _named_people(prompt, cast)
        nodes = []
        for i, t in enumerate(titles):
            who = next((c for c in cast if c in t), lead)
            chapter = chapters[i] if i < len(chapters) else {}
            present = chapter.get("people")
            present = present if isinstance(present, list) else cast[:3]
            beat = {"setting": "the intake atrium", "tone": "wry"}
            first = {
                "title": f"{who} decides",
                "summary": "a choice",
                "people": present + (named if act == 1 and i == 0 else []),
                **beat,
                "children": [],
            }
            nodes.append(
                {
                    "title": t,
                    "summary": f"{who} acts.",
                    "children": [
                        first,
                        {
                            "title": f"{second} answers",
                            "summary": "a cost",
                            "people": present,
                            **beat,
                            "children": [],
                        },
                    ],
                }
            )
        return json.dumps(nodes)
    if "chapter nodes" in prompt or "beat sheet of" in prompt:
        return json.dumps(
            [
                {"title": f"{c}'s turn", "summary": f"{c} changes.", "children": []}
                for c in (cast or [lead])
            ]
        )
    if "Extract the book's canon" in prompt or '"evidence"' in prompt:
        return json.dumps(
            {
                "characters": [
                    {
                        "name": n,
                        "role": "protagonist" if i == 0 else "supporting",
                        "description": f"{n}, from the text.",
                        "aliases": [n.split()[0]],
                        "evidence": _evidence(n, _blocks(prompt).get("source", "")),
                    }
                    for i, n in enumerate(cast)
                ],
                "canon_entries": [],
                "style": {"pov": "third", "tense": "past", "tone": "", "comps": ""},
                "style_signals": (
                    {"narration": "first-person", "narrators": _narrators(prompt)}
                    if _narrators(prompt)
                    else {}
                ),
                "synopsis": f"A story about {', '.join(cast)}.",
            }
        )
    if "Write the full character entries" in prompt:
        return json.dumps(
            {
                "characters": [
                    {
                        "name": n,
                        "role": "protagonist" if i == 0 else "supporting",
                        "description": f"{n}.",
                        "goals": "",
                        "arc": "",
                        "relationships": {},
                        "personality_traits": [],
                        "voice_characteristics": [],
                        "aliases": [n.split()[0]],
                    }
                    for i, n in enumerate(cast)
                ]
            }
        )
    if "Write the book's synopsis" in prompt:
        return json.dumps({"synopsis": f"A story about {', '.join(cast[:4]) or lead}."})
    if "continuity editor" in prompt and "source" in _blocks(prompt):
        # SPEC-73: the journey's source contradicts nothing.
        return json.dumps({"findings": []})
    if "continuity editor" in prompt:
        # One minor finding quoting the first sentence of the first scene: a
        # located finding for the journey to open in the editor (SPEC-31).
        body = prompt.split("<prose>\n", 1)[1].split("\n", 2)[1]
        quote = re.split(r"(?<=[.!?])\s", body, maxsplit=1)[0][:200]
        found = {"type": "other", "severity": "minor", "detail": "Stub finding."}
        return json.dumps({"findings": [{**found, "quote": quote}]})
    if "narrative planner" in prompt:
        return json.dumps(
            [
                {"description": f"{lead} confronts {second}.", "characters": cast[:2]},
                {
                    "description": f"{second} gives ground.",
                    "characters": cast[1:2] or cast,
                },
                {"description": "The silence after.", "characters": cast},
            ]
        )
    if prompt.lstrip().startswith("You ARE") or "\nYou ARE " in prompt:
        m = re.search(r"You ARE (.+?)\.", prompt)
        who = m.group(1) if m else lead
        return json.dumps(
            {
                "intent": "to be heard",
                "actions": [f"{who} leans in."],
                "lines": [f'"Listen," {who} said.'],
                "interiority": "doubt",
            }
        )
    if "Review this draft" in prompt:
        present = re.search(r"Characters present:\s*(.+)", prompt)
        names = [n.strip() for n in present.group(1).split(",")] if present else cast
        beat = (re.search(r"The beat to cover: (.*)", prompt) or [""])[0]
        _REVIEWS[beat] += 1
        again = _REVIEWS[beat] % _STATE["ensemble_rounds"] != 0  # another round
        note = {"character": None, "severity": "blocking", "note": "stub: again"}
        return json.dumps(
            {
                "satisfied": not again,
                "arc_fit": 0.9,
                "beat_coverage": 0.9,
                "characters_acting": names,
                "objections": [note] if again else [],
            }
        )
    if '"score"' in prompt or "Score" in prompt and "JSON" in prompt:
        return json.dumps({"score": 0.75, "explanation": "stub judge"})
    # Prose.
    people = " and ".join(cast[:3]) or "The two of them"
    beat = re.search(rf"(?m)^{re.escape(BEAT_TO_WRITE_LABEL)}: (.+)$", prompt)
    if beat:
        # Built from the beat itself: every cast member is named, each in a
        # clause with no fixed run of six words, so consecutive beats never
        # repeat a phrase at the seam (R4) and each beat's prose is unique in
        # its scene (revise beat mode can find it).
        what = " ".join(beat.group(1).rstrip(".!? ").split()[:14])
        named = cast[:3] or [lead]
        if (
            "Procurement" in prompt and f"{CURRENT_BEAT_MARK} 1." in prompt
        ):  # SPEC-48 journey seed: an invention the brief does not name
            what += ", thinking of Gary"
        return " ".join(f"{name} weighed it: {what}." for name in named)
    return (
        f"{people} stood in the doorway while the rain kept on. "
        f"{lead} turned first, and said nothing for a while. "
        f'"We go on," {second} said at last, and {lead} nodded, '
        "because there was nothing else to do."
    )


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    prompt = "\n".join(
        m.get("content", "") for m in body.get("messages", []) if isinstance(m, dict)
    )
    _STATE["calls"] += 1
    _LOG.append({"t": time.time(), "model": body.get("model"), "prompt": prompt})
    if _STATE["fail_429"] > 0:
        _STATE["fail_429"] -= 1
        return JSONResponse(
            {"error": {"message": "RESOURCE_EXHAUSTED (stub)", "code": 429}},
            status_code=429,
            headers={"retry-after": str(_STATE["retry_after"])},
        )
    text = _answer(prompt)
    rf = body.get("response_format") or {}
    if rf.get("type") == "json_object" and text.lstrip().startswith("["):
        text = json.dumps({"findings": json.loads(text)})
    words = max(1, len(prompt) // 4)
    return {
        "id": f"stub-{_STATE['calls']}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model", "stub"),
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": text},
            }
        ],
        "usage": {
            "prompt_tokens": words,
            "completion_tokens": max(1, len(text) // 4),
            "total_tokens": words + max(1, len(text) // 4),
        },
    }


@app.post("/__stub/config")
async def configure(request: Request):
    body = await request.json()
    for k in ("fail_429", "retry_after", "ensemble_rounds"):
        if k in body:
            _STATE[k] = int(body[k])
    return _STATE


@app.post("/__stub/reset")
async def reset():
    _STATE.update({"fail_429": 0, "retry_after": 1, "calls": 0, "ensemble_rounds": 1})
    _LOG.clear()
    _REVIEWS.clear()
    return _STATE


@app.get("/__stub/requests")
async def requests_log(limit: int = 50):
    return {"calls": _STATE["calls"], "requests": list(_LOG)[-limit:]}


def main() -> None:
    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8099)
    args = ap.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
