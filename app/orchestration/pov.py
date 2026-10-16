"""A scene's point of view (SPEC-24), stated the same way to the planner, the
beat prose and revise. `Pov.one_head` is the one wide-mode rule."""

from __future__ import annotations

import re
from typing import NamedTuple, Optional

from app.core.llm_text import clean_for_llm
from app.core.prompt_markers import (
    POV_LABEL,
    POV_LIMIT_CLOSE,
    POV_LIMIT_OPEN,
    POV_WIDE_OPEN,
)

DEFAULT_POV_MODE = "third person limited"
# "person" is optional: style.pov also stores the short forms the extraction
# writes ("first", "third-limited", "third-omniscient").
_POV_MODE = re.compile(
    r"\b(first|second|third)(?:[ -]person)?(?:[ ,-]+(limited|omniscient|objective))?\b",
    re.I,
)
# What a wide mode's narrator may do. A mode not listed stays in one head.
_WIDE = {
    "omniscient": "may move between characters' thoughts:",
    "objective": (
        "reports only what can be seen and heard: no one's thoughts or feelings "
        "stated,"
    ),
}


def pov_mode(text) -> Optional[str]:
    """The narrative mode a free-text POV names ("Third-person limited, on
    Milo" -> "third person limited"), or None."""
    m = _POV_MODE.search(str(text or ""))
    if not m:
        return None
    person = m.group(1).lower()
    # An unqualified third ("third", "close third") is read as limited.
    view = m.group(2) or ("limited" if person == "third" else None)
    return " ".join(w for w in (person, "person", view) if w).lower()


class Pov(NamedTuple):
    """A scene's point of view: the narrative mode, and whose head the scene
    stays in (a cast member's name), if anyone's."""

    mode: str
    who: Optional[str] = None

    @property
    def one_head(self) -> bool:
        """Held to one head: every mode but omniscient and objective."""
        return self.mode.rsplit(" ", 1)[-1] not in _WIDE

    @property
    def label(self) -> str:
        """ "<mode>, <who>" (or the mode alone): the text after "Point of view: "."""
        return f"{self.mode}, {self.who}" if self.who else self.mode


# The run of capitalised words a style-guide POV names its character with
# ("on Dr. Ada Vance").
_CAPITALISED_RUN = re.compile(r"[A-Z][\w'’-]*\.?(?:\s+[A-Z][\w'’-]*\.?)*")


def resolve_pov(scene_request: dict, canon) -> Pov:
    """The scene's point of view (SPEC-24, W2-4).

    The character is the request's POV character (a cast name, as the API
    writes it), else the cast member the book's style guide names. The mode is
    the one the style guide names (style.pov is free text: "third person
    limited, on Milo Voss"), else third person limited. Both are stated, so the
    model knows whose head the scene stays in."""
    style_pov = getattr(getattr(canon, "style", None), "pov", None)
    mode = pov_mode(style_pov) or DEFAULT_POV_MODE
    who = " ".join(clean_for_llm(str(scene_request.get("pov_character") or "")).split())
    return Pov(mode, who or _style_pov_character(style_pov, canon))


def _style_pov_character(style_pov, canon) -> Optional[str]:
    """The cast member a style-guide POV names ("third person limited, on Dr.
    Ada Vance" -> "Ada Vance"), or None: the first capitalised run outside the
    mode that resolves to the cast, dropping leading words ("Dr.") until it
    does. Free text is never promoted to a character."""
    from app.planning.outline import CastIndex

    cast = CastIndex(getattr(canon, "characters", None) or [])
    rest = _POV_MODE.sub(" ", clean_for_llm(str(style_pov or "")))
    for run in _CAPITALISED_RUN.finditer(rest):
        words = run.group(0).rstrip(".").split()
        for i in range(len(words)):
            cid = cast.resolve(" ".join(words[i:]))
            if cid is not None:
                return cast.names[cid]
    return None


# The one naming rule of the words the author keeps (SPEC-48), printed by the
# beat prose and revise prompts after the POV rule.
NAMING_RULE = (
    "Name only people in the canon, the cast or 'Also named'. Anyone else stays "
    "unnamed: 'a man from Procurement'."
)
# Wide and one-head modes alike: the hindsight two drafts narrated (SPEC-48).
_NO_FORESHADOWING = "no foreshadowing — never 'would later learn'"


def pov_instruction(pov: Pov) -> str:
    """The POV paragraph of the beat prompt and the revise prompt (SPEC-24):
    mode and character on one line, then what the mode allows. A one-head mode
    names no one before the POV character learns their name; a wide mode may
    leave one head, but neither tells what has not happened yet."""
    head = f"{POV_LABEL}: {pov.label}."
    if not pov.one_head:
        allowed = _WIDE[pov.mode.rsplit(" ", 1)[-1]]
        return f"{head} {POV_WIDE_OPEN} {allowed} no aftermath, {_NO_FORESHADOWING}."
    who = pov.who or "the point-of-view character"
    learns = f"{pov.who} learns" if pov.who else "they learn"
    stay = " Stay in their head from the first line to the last." if pov.who else ""
    return (
        f"{head} {POV_LIMIT_OPEN} {who} {POV_LIMIT_CLOSE}: no aftermath, "
        f"{_NO_FORESHADOWING}, no naming people before {learns} their name in the "
        f"story; an introduction in this scene counts.{stay} The brief may say what "
        f"other people intend or feel; show only what {who} can see or hear."
    )


def plan_pov_instruction(pov: Pov) -> str:
    """`pov_instruction`, plus (one head) a plan that stays in that head and
    lets the POV character learn a name before the prose uses it (W3-3)."""
    if not pov.one_head:
        return pov_instruction(pov)
    who = pov.who or "the point-of-view character"
    return (
        f"{pov_instruction(pov)} Plan only what {who} perceives. If a beat needs a "
        f"person named, plan the moment {who} learns the name (an introduction, a "
        "nameplate, a form)."
    )
